#!/usr/bin/env python3
"""Tests for the bi-temporal memory graph.

The point of this layer is that memory can be **corrected**. So the tests focus
on the properties an append-only log cannot provide:

  * a contradicting fact invalidates the old one instead of coexisting with it
  * nothing is ever deleted - history stays answerable
  * "what was true then" and "what is true now" are different questions
  * provenance is mandatory: a fact with no episode cannot be created
  * recall is token-bounded, and returns current truth by default
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import memgraph as m  # noqa: E402


class GraphCase(unittest.TestCase):
    """Every test runs against a private in-memory database."""

    def setUp(self) -> None:
        self.con = m.connect(":memory:")
        m.init(self.con)
        self.episode = m.add_episode(self.con, "the Owner said so", source="test")

    def tearDown(self) -> None:
        self.con.close()

    def fact(self, subject, predicate, obj, **kw):
        kw.setdefault("episode_id", self.episode)
        return m.assert_fact(self.con, subject, predicate, obj, **kw)


class Schema(GraphCase):
    def test_init_is_idempotent(self):
        m.init(self.con)
        m.init(self.con)
        self.assertEqual(m.stats(self.con)["facts"], 0)

    def test_existing_megamind_tables_are_untouched(self):
        """The migration must be additive - megamind.py keeps working."""
        self.con.execute("CREATE TABLE memories(id INTEGER PRIMARY KEY, ts REAL, "
                         "type TEXT, topic TEXT, text TEXT, tags TEXT)")
        self.con.execute("INSERT INTO memories(ts,type,topic,text) VALUES(1,'x','y','z')")
        m.init(self.con)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1)


class Provenance(GraphCase):
    def test_a_fact_requires_a_real_episode(self):
        """Alfred must never fabricate: a fact with no source is a bug."""
        with self.assertRaises(m.MemoryGraphError):
            m.assert_fact(self.con, "a", "likes", "b", episode_id=99999)

    def test_an_episode_requires_text(self):
        for bad in ("", "   "):
            with self.assertRaises(m.MemoryGraphError):
                m.add_episode(self.con, bad)

    def test_every_fact_cites_its_episode(self):
        outcome = self.fact("owner", "prefers", "dark mode")
        rows = m.current(self.con, subject="owner")
        self.assertEqual(rows[0]["episodeId"], self.episode)
        self.assertIsNotNone(m.episode(self.con, self.episode))
        self.assertEqual(outcome.created, True)

    def test_doctor_finds_no_orphans_in_a_healthy_graph(self):
        self.fact("owner", "prefers", "dark mode")
        self.assertEqual(m.orphan_facts(self.con), [])


class Validation(GraphCase):
    def test_predicate_is_required(self):
        with self.assertRaises(m.MemoryGraphError):
            self.fact("a", "  ", "b")

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(m.MemoryGraphError):
            self.fact("a", "likes", "b", kind="vibes")

    def test_confidence_must_be_a_probability(self):
        for bad in (-0.5, 1.5):
            with self.assertRaises(m.MemoryGraphError):
                self.fact("a", "likes", "b", confidence=bad)

    def test_entity_needs_a_name(self):
        with self.assertRaises(m.MemoryGraphError):
            m.upsert_entity(self.con, "   ")


class Contradiction(GraphCase):
    """The headline capability: memory that can be corrected."""

    def test_a_new_value_invalidates_the_previous_one(self):
        first = self.fact("owner", "prefers", "Adidas", kind="preference")
        second = self.fact("owner", "prefers", "Nike", kind="preference")

        self.assertIn(first.fact_id, second.invalidated)
        live = m.current(self.con, subject="owner", predicate="prefers")
        self.assertEqual(len(live), 1, "exactly one value may be current")
        self.assertEqual(live[0]["object"], "Nike")

    def test_the_superseded_fact_is_kept_not_deleted(self):
        first = self.fact("owner", "prefers", "Adidas", kind="preference")
        self.fact("owner", "prefers", "Nike", kind="preference")

        rows = m.history(self.con, "owner", "prefers")
        self.assertEqual(len(rows), 2, "history must retain both")
        old = next(r for r in rows if r["id"] == first.fact_id)
        self.assertFalse(old["live"])
        self.assertIsNotNone(old["tInvalid"], "world time must record when it stopped")
        self.assertIsNotNone(old["tExpired"], "system time must record when we learned")

    def test_the_old_fact_points_at_what_replaced_it(self):
        first = self.fact("owner", "prefers", "Adidas")
        second = self.fact("owner", "prefers", "Nike")
        old = next(r for r in m.history(self.con, "owner", "prefers") if r["id"] == first.fact_id)
        self.assertEqual(old["supersededBy"], second.fact_id)

    def test_invalidation_uses_the_new_facts_valid_time(self):
        """World time and system time are independent axes."""
        past = time.time() - 5000
        self.fact("owner", "prefers", "Adidas", t_valid=past - 1000)
        switch = past + 100
        self.fact("owner", "prefers", "Nike", t_valid=switch)
        old = [r for r in m.history(self.con, "owner", "prefers") if not r["live"]][0]
        self.assertAlmostEqual(old["tInvalid"], switch, places=3)

    def test_repeating_the_same_fact_is_not_a_change(self):
        first = self.fact("owner", "prefers", "Nike")
        again = self.fact("owner", "prefers", "Nike")
        self.assertFalse(again.created)
        self.assertEqual(again.duplicate_of, first.fact_id)
        self.assertEqual(len(m.history(self.con, "owner", "prefers")), 1)

    def test_multi_valued_predicates_coexist(self):
        self.fact("alfred", "depends_on", "sqlite", single_valued=False)
        self.fact("alfred", "depends_on", "python", single_valued=False)
        live = m.current(self.con, subject="alfred", predicate="depends_on")
        self.assertEqual(len(live), 2)

    def test_different_predicates_do_not_interfere(self):
        self.fact("owner", "prefers", "Nike")
        self.fact("owner", "uses", "Windows")
        self.assertEqual(len(m.current(self.con, subject="owner")), 2)

    def test_retract_marks_a_fact_dead_without_deleting_it(self):
        outcome = self.fact("owner", "prefers", "Nike")
        self.assertTrue(m.retract_fact(self.con, outcome.fact_id))
        self.assertEqual(m.current(self.con, subject="owner", predicate="prefers"), [])
        self.assertEqual(len(m.history(self.con, "owner", "prefers")), 1)

    def test_retracting_twice_reports_no_change(self):
        outcome = self.fact("owner", "prefers", "Nike")
        m.retract_fact(self.con, outcome.fact_id)
        self.assertFalse(m.retract_fact(self.con, outcome.fact_id))


class TimeTravel(GraphCase):
    def test_as_of_returns_what_was_true_then(self):
        t0 = time.time() - 10_000
        t1 = time.time() - 5_000
        self.fact("owner", "prefers", "Adidas", t_valid=t0)
        self.fact("owner", "prefers", "Nike", t_valid=t1)

        earlier = m.as_of(self.con, t0 + 100, subject="owner", predicate="prefers")
        self.assertEqual([f["object"] for f in earlier], ["Adidas"])

        later = m.as_of(self.con, t1 + 100, subject="owner", predicate="prefers")
        self.assertEqual([f["object"] for f in later], ["Nike"])

    def test_as_of_before_anything_was_known_is_empty(self):
        self.fact("owner", "prefers", "Nike", t_valid=time.time())
        self.assertEqual(m.as_of(self.con, time.time() - 100_000, subject="owner"), [])

    def test_current_and_as_of_now_agree(self):
        self.fact("owner", "prefers", "Nike")
        now = time.time() + 1
        self.assertEqual(
            [f["object"] for f in m.current(self.con, subject="owner")],
            [f["object"] for f in m.as_of(self.con, now, subject="owner")],
        )


class Traversal(GraphCase):
    def test_neighbours_follows_entity_edges(self):
        self.fact("alfred", "uses", "sqlite", object_kind="tool", single_valued=False)
        self.fact("sqlite", "provides", "fts5", object_kind="feature", single_valued=False)
        one_hop = m.neighbours(self.con, "alfred", hops=1)
        self.assertTrue(any(f["object"] == "sqlite" for f in one_hop))

    def test_two_hops_reaches_further_than_one(self):
        self.fact("alfred", "uses", "sqlite", object_kind="tool", single_valued=False)
        self.fact("sqlite", "provides", "fts5", object_kind="feature", single_valued=False)
        two = m.neighbours(self.con, "alfred", hops=2)
        self.assertTrue(any(f["object"] == "fts5" for f in two))

    def test_hops_are_capped_so_traversal_cannot_run_away(self):
        self.fact("a", "links", "b", object_kind="concept", single_valued=False)
        # Should not raise or hang even with an absurd request.
        m.neighbours(self.con, "a", hops=99)

    def test_superseded_edges_are_not_traversed(self):
        self.fact("alfred", "uses", "old-tool", object_kind="tool")
        self.fact("alfred", "uses", "new-tool", object_kind="tool")
        found = {f["object"] for f in m.neighbours(self.con, "alfred", hops=1)}
        self.assertIn("new-tool", found)
        self.assertNotIn("old-tool", found)

    def test_an_unknown_entity_yields_nothing(self):
        self.assertEqual(m.neighbours(self.con, "nobody"), [])


class Search(GraphCase):
    def test_keyword_search_finds_a_statement(self):
        self.fact("owner", "prefers", "dark mode",
                  statement="The Owner prefers dark mode in every editor")
        hits = m.search_keyword(self.con, "dark mode editor")
        self.assertTrue(hits)

    def test_search_text_cannot_inject_fts_syntax(self):
        self.fact("owner", "prefers", "x", statement="plain statement")
        for hostile in ('" OR "', "NEAR(", "*", 'a" AND "b'):
            m.search_keyword(self.con, hostile)  # must not raise

    def test_empty_query_returns_nothing(self):
        self.assertEqual(m.search_keyword(self.con, ""), [])
        self.assertEqual(m.search_keyword(self.con, "!!"), [])

    def test_vector_search_is_empty_without_an_embedding(self):
        self.assertEqual(m.search_vector(self.con, None), [])

    def test_vector_search_ranks_by_cosine(self):
        self.fact("a", "is", "one", statement="one", embedding=[1.0, 0.0])
        self.fact("b", "is", "two", statement="two", embedding=[0.0, 1.0])
        hits = m.search_vector(self.con, [1.0, 0.0])
        self.assertTrue(hits)
        top = m.current(self.con, subject="a")[0]["id"]
        self.assertEqual(hits[0][0], top)

    def test_embeddings_round_trip(self):
        vector = [0.5, -0.25, 1.0]
        packed = m.pack_embedding(vector)
        for a, b in zip(m.unpack_embedding(packed), vector):
            self.assertAlmostEqual(a, b, places=6)

    def test_cosine_handles_degenerate_input(self):
        self.assertEqual(m.cosine([], [1.0]), 0.0)
        self.assertEqual(m.cosine([0.0, 0.0], [1.0, 1.0]), 0.0)
        self.assertAlmostEqual(m.cosine([1.0, 0.0], [1.0, 0.0]), 1.0, places=6)

    def test_rrf_rewards_agreement_between_rankings(self):
        fused = dict(m.reciprocal_rank_fusion([(1, 9.0), (2, 8.0)], [(2, 0.9), (3, 0.8)]))
        self.assertGreater(fused[2], fused[1], "an item ranked by both should win")

    def test_rrf_of_nothing_is_empty(self):
        self.assertEqual(m.reciprocal_rank_fusion(), [])


class RecallBudget(GraphCase):
    def _many(self, n=40):
        for i in range(n):
            m.assert_fact(self.con, f"entity{i}", "relates_to", "alfred harness memory",
                          episode_id=self.episode, single_valued=False,
                          statement=f"Fact number {i} about the alfred harness memory graph "
                                    f"with enough words to consume budget")

    def test_recall_respects_the_token_budget(self):
        self._many()
        result = m.recall(self.con, "alfred harness memory", k=30, max_tokens=60)
        self.assertLessEqual(result.tokens, 60)
        self.assertTrue(result.truncated)

    def test_a_generous_budget_is_not_truncated(self):
        self.fact("owner", "prefers", "dark mode", statement="The Owner prefers dark mode")
        result = m.recall(self.con, "dark mode", max_tokens=400)
        self.assertFalse(result.truncated)

    def test_recall_returns_current_truth_not_stale_values(self):
        """The bug this whole module exists to fix."""
        self.fact("owner", "prefers", "Adidas", kind="preference",
                  statement="The Owner prefers Adidas sneakers")
        self.fact("owner", "prefers", "Nike", kind="preference",
                  statement="The Owner prefers Nike sneakers")
        result = m.recall(self.con, "sneakers the Owner prefers")
        blob = result.context
        self.assertIn("Nike", blob)
        self.assertNotIn("Adidas", blob, "superseded preferences must not resurface")

    def test_history_can_be_requested_explicitly(self):
        self.fact("owner", "prefers", "Adidas", statement="The Owner prefers Adidas sneakers")
        self.fact("owner", "prefers", "Nike", statement="The Owner prefers Nike sneakers")
        result = m.recall(self.con, "sneakers Adidas Nike", include_history=True)
        self.assertIn("Adidas", result.context)

    def test_recall_cites_its_sources(self):
        self.fact("owner", "prefers", "dark mode", statement="The Owner prefers dark mode")
        result = m.recall(self.con, "dark mode")
        self.assertEqual(result.sources, [self.episode])

    def test_recall_on_an_empty_graph_is_harmless(self):
        result = m.recall(self.con, "anything")
        self.assertEqual(result.facts, [])
        self.assertEqual(result.context, "")

    def test_traversal_can_seed_recall(self):
        self.fact("alfred", "uses", "sqlite", object_kind="tool", single_valued=False,
                  statement="Alfred uses SQLite for memory")
        result = m.recall(self.con, "storage", seed_entity="alfred", hops=1)
        self.assertTrue(result.facts, "traversal should contribute when keywords miss")

    def test_recall_is_serializable(self):
        import json
        self.fact("owner", "prefers", "dark mode")
        json.dumps(m.recall(self.con, "dark").to_dict())

    def test_a_tiny_budget_still_returns_the_best_match_clipped(self):
        """Reporting "nothing relevant" while relevant facts exist is lying by omission."""
        self.fact("owner", "prefers", "dark mode", statement=(
            "The Owner prefers dark mode in every editor and terminal he uses, "
            "and has said so repeatedly across many long sessions, including "
            "when configuring the dashboard, the Ultron CLI, VS Codium, the "
            "Windows terminal profile, and every other surface he touches daily"))
        result = m.recall(self.con, "dark mode editor terminal", max_tokens=30)
        self.assertEqual(len(result.facts), 1, "the best match must survive")
        self.assertTrue(result.context.endswith("..."), f"must be clipped: {result.context!r}")
        self.assertTrue(result.truncated)
        self.assertLessEqual(result.tokens, 32)

    def test_no_matches_is_distinguishable_from_a_tight_budget(self):
        self.fact("owner", "prefers", "dark mode", statement="The Owner prefers dark mode")
        nothing = m.recall(self.con, "quantum chromodynamics tractors")
        self.assertEqual(nothing.candidates, 0)
        self.assertFalse(nothing.budget_limited)

        self._many(20)
        squeezed = m.recall(self.con, "alfred harness memory", k=20, max_tokens=60)
        self.assertGreater(squeezed.candidates, len(squeezed.facts))
        self.assertTrue(squeezed.budget_limited)

    def test_candidates_counts_matches_before_the_budget(self):
        self._many(10)
        result = m.recall(self.con, "alfred harness memory", k=10, max_tokens=4000)
        self.assertEqual(result.candidates, len(result.facts))
        self.assertFalse(result.budget_limited)


class Backfill(GraphCase):
    def _seed_memories(self, rows):
        self.con.execute("CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, ts REAL,"
                         " type TEXT, topic TEXT, text TEXT, tags TEXT)")
        for i, (mtype, topic, text) in enumerate(rows, start=1):
            self.con.execute("INSERT INTO memories(id,ts,type,topic,text,tags) VALUES(?,?,?,?,?,'')",
                             (i, time.time() - 1000 + i, mtype, topic, text))
        self.con.commit()

    def test_backfill_creates_an_episode_and_fact_per_memory(self):
        self._seed_memories([("decision", "routing", "Route trivial work to the local model"),
                             ("learning", "crlf", "Never HMAC raw bytes of a git-tracked file")])
        report = m.backfill_from_megamind(self.con)
        self.assertEqual(report["episodes"], 2)
        self.assertEqual(report["facts"], 2)
        self.assertEqual(m.orphan_facts(self.con), [])

    def test_backfill_is_idempotent(self):
        self._seed_memories([("decision", "a", "only once")])
        m.backfill_from_megamind(self.con)
        second = m.backfill_from_megamind(self.con)
        self.assertEqual(second["episodes"], 0)
        self.assertGreaterEqual(second["skipped"], 1)

    def test_backfilled_notes_do_not_invalidate_each_other(self):
        """Historical notes are observations, not one current value."""
        self._seed_memories([("learning", "topic", "first note"),
                             ("learning", "topic", "second note")])
        m.backfill_from_megamind(self.con)
        live = m.current(self.con, subject="topic", predicate="recorded")
        self.assertEqual(len(live), 2)

    def test_backfill_without_a_memories_table_is_harmless(self):
        report = m.backfill_from_megamind(self.con)
        self.assertEqual(report["episodes"], 0)


class EntityResolution(GraphCase):
    """Identity is the name. Splitting an entity by kind silently breaks traversal."""

    def test_the_same_name_is_one_entity_regardless_of_kind(self):
        first = m.upsert_entity(self.con, "sqlite", "concept")
        second = m.upsert_entity(self.con, "sqlite", "tool")
        self.assertEqual(first, second, "one name must mean one node")

    def test_a_specific_kind_upgrades_a_placeholder(self):
        m.upsert_entity(self.con, "sqlite", "concept")
        m.upsert_entity(self.con, "sqlite", "tool")
        row = self.con.execute("SELECT kind FROM mg_entity WHERE name='sqlite'").fetchone()
        self.assertEqual(row["kind"], "tool")

    def test_a_specific_kind_is_never_downgraded(self):
        m.upsert_entity(self.con, "sqlite", "tool")
        m.upsert_entity(self.con, "sqlite", "concept")
        row = self.con.execute("SELECT kind FROM mg_entity WHERE name='sqlite'").fetchone()
        self.assertEqual(row["kind"], "tool")

    def test_names_are_trimmed_so_whitespace_is_not_identity(self):
        a = m.upsert_entity(self.con, "alfred")
        b = m.upsert_entity(self.con, "  alfred  ")
        self.assertEqual(a, b)

    def test_a_subject_and_an_object_share_one_node(self):
        """The bug this fix addresses: traversal must cross subject/object roles."""
        self.fact("alfred", "uses", "sqlite", object_kind="tool", single_valued=False)
        self.fact("sqlite", "provides", "fts5", object_kind="feature", single_valued=False)
        count = self.con.execute(
            "SELECT COUNT(*) AS n FROM mg_entity WHERE name='sqlite'").fetchone()["n"]
        self.assertEqual(count, 1)

    def test_summaries_evolve_without_duplicating_the_entity(self):
        m.upsert_entity(self.con, "alfred", summary="first")
        m.upsert_entity(self.con, "alfred", summary="second")
        rows = list(self.con.execute("SELECT summary FROM mg_entity WHERE name='alfred'"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["summary"], "second")


class Stats(GraphCase):
    def test_stats_counts_live_and_superseded_separately(self):
        self.fact("owner", "prefers", "Adidas")
        self.fact("owner", "prefers", "Nike")
        s = m.stats(self.con)
        self.assertEqual(s["facts"], 2)
        self.assertEqual(s["liveFacts"], 1)
        self.assertEqual(s["supersededFacts"], 1)

    def test_stats_groups_by_kind(self):
        self.fact("owner", "prefers", "x", kind="preference")
        self.fact("alfred", "decided", "y", kind="decision")
        self.assertEqual(set(m.stats(self.con)["byKind"]), {"preference", "decision"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
