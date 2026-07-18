#!/usr/bin/env python3
"""Offline tests for Ultron's pure logic (no network, no model needed).

Run:  python scripts/test_ultron.py      (or: python -m pytest scripts/test_ultron.py)

These cover the deterministic brain-assembly logic: frontmatter stripping, URI
resolution, skill-name parsing, model resolution, agent loading, and system-prompt
assembly. The network/streaming paths are intentionally not exercised here.
"""
import sys
import unittest
from pathlib import Path

# Allow `import ultron` whether run from repo root or the scripts/ dir.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ultron  # noqa: E402


class TestStripFrontmatter(unittest.TestCase):
    def test_with_frontmatter(self):
        text = "---\nname: coding\ninclusion: always\n---\n# Body\nhello"
        body, meta = ultron.strip_frontmatter(text)
        self.assertEqual(meta["name"], "coding")
        self.assertEqual(meta["inclusion"], "always")
        self.assertTrue(body.startswith("# Body"))
        self.assertNotIn("inclusion:", body)

    def test_without_frontmatter(self):
        body, meta = ultron.strip_frontmatter("# Just a doc\ntext")
        self.assertEqual(meta, {})
        self.assertTrue(body.startswith("# Just a doc"))


class TestResolveUri(unittest.TestCase):
    def test_file_uri_absolute(self):
        p = ultron.resolve_uri("file:///C:/Alfred/.kiro/brains/alfred-qa/identity.txt")
        self.assertTrue(str(p).replace("\\", "/").endswith("Alfred/.kiro/brains/alfred-qa/identity.txt"))
        self.assertTrue(p.is_absolute())

    def test_relative_path_resolves_under_root(self):
        p = ultron.resolve_uri("scripts/ultron.py")
        self.assertEqual(p, ultron.ROOT / "scripts" / "ultron.py")


class TestParseSkillNames(unittest.TestCase):
    def test_three_skills_with_hyphen(self):
        names = ultron.parse_skill_names("Load the quality-assurance, coding, and debugging skills.")
        self.assertEqual(names, ["quality-assurance", "coding", "debugging"])

    def test_two_skills(self):
        self.assertEqual(ultron.parse_skill_names("Load the product and architecture skills."),
                         ["product", "architecture"])

    def test_none(self):
        self.assertEqual(ultron.parse_skill_names("No skills line here."), [])


class TestResolveModel(unittest.TestCase):
    def test_requested_present(self):
        self.assertEqual(ultron.resolve_model("m1", ["m1", "m2"], quiet=True), "m1")

    def test_requested_absent_uses_first(self):
        self.assertEqual(ultron.resolve_model("nope", ["only-loaded"], quiet=True), "only-loaded")

    def test_unknown_list_returns_requested(self):
        self.assertEqual(ultron.resolve_model("m1", None, quiet=True), "m1")
        self.assertEqual(ultron.resolve_model("m1", [], quiet=True), "m1")


class TestAgentLoading(unittest.TestCase):
    def test_list_agents_includes_new_ones(self):
        stems = {p.stem for p in ultron.list_agent_files()}
        for expected in ("alfred-qa", "alfred-integrations", "alfred-product"):
            self.assertIn(expected, stems)

    def test_load_agent_qa(self):
        agent = ultron.load_agent("alfred-qa")
        self.assertEqual(agent["name"], "alfred-qa")
        self.assertTrue(agent["identity"])
        self.assertIn("Load the quality-assurance", agent["identity"])
        self.assertEqual(agent["model"], "claude-opus-4.6")

    def test_unknown_agent_exits(self):
        with self.assertRaises(SystemExit) as ctx:
            ultron.load_agent("does-not-exist-xyz")
        self.assertEqual(ctx.exception.code, 2)


class TestSteeringAndAssembly(unittest.TestCase):
    def test_steering_loads_without_frontmatter(self):
        s = ultron.load_steering()
        self.assertTrue(s)
        # The 'inclusion: always' frontmatter key must be stripped from the body.
        self.assertNotIn("inclusion: always", s)

    def test_assemble_minimal(self):
        agent = {"identity": "I am a test agent.", "name": "t", "model": "", "description": ""}
        out = ultron.assemble_system_prompt(agent, steering=False, skills=False, memory_text="")
        self.assertIn("I am a test agent.", out)
        self.assertIn("operating inside Ultron", out)
        self.assertNotIn("Always-on operating rules", out)

    def test_assemble_with_steering_and_memory(self):
        agent = {"identity": "ID", "name": "t", "model": "", "description": ""}
        out = ultron.assemble_system_prompt(
            agent, steering=True, skills=False, memory_text="- (fact) x: y")
        self.assertIn("Always-on operating rules", out)
        self.assertIn("Relevant remembered context", out)
        self.assertIn("- (fact) x: y", out)


class TestParserSmoke(unittest.TestCase):
    def test_parser_builds_and_dry_run_parses(self):
        parser = ultron.build_parser()
        args = parser.parse_args(["run", "--agent", "alfred-qa", "--dry-run", "hello"])
        self.assertEqual(args.agent, "alfred-qa")
        self.assertTrue(args.dry_run)
        self.assertEqual(args.backend, "local")


if __name__ == "__main__":
    unittest.main(verbosity=2)
