# ADR 002 — A Windows-native and Linux-native terminal

> **Status:** proposed (2026-08-31). **Not started.** This records the decision and its
> reasoning while the research is fresh, so that when work begins it starts from a
> position rather than from a blank page. The evidence is in
> [`docs/terminal-research.md`](../terminal-research.md); every external claim there
> carries a source URL.

---

## Context

The Owner wants his own terminal, with [Ghostty](https://github.com/ghostty-org/ghostty)
as the inspiration, and one explicit difference: Ghostty is **macOS- and Linux-native**,
and ours must be **Windows-native and Linux-native**.

Three facts from the research reframe the problem:

1. **Ghostty is a library with frontends, not an app.** `libghostty` exposes the terminal
   core over a C ABI; the macOS frontend is Swift/AppKit and the Linux frontend is Zig
   against GTK4. A third platform is an addition, not a rewrite.
2. **The terminal core is being handed out.** `libghostty-vt` is a zero-dependency (not
   even libc) VT parser and state machine that **already supports Windows as a build
   target**.
3. **Ghostty's lack of Windows support is a manpower and prioritization decision, not a
   technical impossibility** — and the team's stated intent is a *native* Windows UI with
   a Direct3D renderer, explicitly not a GTK port. That is the same judgement the Owner
   is making.

Against that, one fact constrains the ceiling: **ConPTY re-interprets and regenerates the
VT stream** rather than passing it through, which loses information, reorders operations
and truncates unrecognised sequences. Passthrough mode exists but still trashes the
buffer on entry/exit. This is upstream of anything we can write.

## Decision

**Build it in Rust, with a core/frontend split, embedding `libghostty-vt` for terminal
emulation, `wgpu` for rendering, `winit` for windowing, and a platform-specific font
stack (DirectWrite on Windows, FreeType + HarfBuzz on Linux).**

Explicitly:

| Concern | Choice | Why |
|---|---|---|
| Language | **Rust** | WezTerm, Alacritty and Rio are three shipping existence proofs of a single cross-platform codebase, and the crate ecosystem (winit, wgpu, portable-pty) already covers the boring parts. |
| Terminal core | **`alacritty_terminal` now, `libghostty-vt` later, behind our own trait** | Terminal emulation is a multi-year correctness problem — Ghostty is *still* finding edge cases after 3+ years, so writing our own is the least defensible option. But `libghostty-vt`'s own header says the API "is not yet stable and is definitely going to change", so starting there would be building on a moving target. `alacritty_terminal` is published, pure Rust and proven on ConPTY; Zed embeds it for exactly this reason. The trait is defined **first**, so the swap is a swap. |
| Architecture | **core library + thin native frontends** | Copied from Ghostty deliberately. It is the decision that makes the second platform cheap. |
| GPU | **wgpu** (D3D12 on Windows, Vulkan on Linux) | One renderer instead of two is the largest single saving available. Rio proves it works. |
| Windowing | **winit** | Proven in Alacritty and Rio. Accepted cost: no native widgets, so tabs and the palette are ours to draw. |
| Fonts | **DirectWrite (Win) / FreeType+HarfBuzz (Linux)** | This is the one place abstraction is refused. ClearType-grade text is what Windows users compare against, and "native" is felt in the font rendering before anywhere else. |
| PTY | **ConPTY on Windows, Unix PTY on Linux**, behind one trait | There is no alternative on Windows. Isolating it means the known limitations live in one file. |

## Consequences

**Accepted:**

- **Windows fidelity is capped by ConPTY, not by us.** Some sequences will round-trip
  imperfectly no matter how correct our emulator is. This will be documented rather than
  quietly absorbed as our own bug.
- **No native widgets from winit.** Tabs, splits and the command palette are drawn by us.
  Alacritty shows this is viable; it is still work.
- **A C-ABI dependency on a young library.** `libghostty-vt` is being extracted right now and
  its header states plainly that the API will change. That is why it is the *target* rather
  than the starting point, and why `alacritty_terminal` is the starting point rather than the
  fallback. Naming which is which turned out to matter: "fallback" implied we would begin on
  libghostty-vt and retreat, which the evidence says is backwards.
- **No UI Automation from winit at all**, so accessibility is ours to build. Budgeted as a
  launch requirement (see success criteria), not a follow-up — retrofitting it is a rewrite of
  the input and text-model layer.
- **Snap layouts and Mica need raw HWND work.** winit exposes neither; both are reachable
  through the raw window handle, and both are cosmetic-until-they-aren't on Windows 11.
- **wgpu's abstraction overhead is unmeasured for this workload.** It must be benchmarked
  against the stated trigger before the renderer is considered settled, because AtlasEngine's
  numbers come from exploiting the monospace grid directly.

**Rejected, with reasons:**

- **Zig.** Ghostty proves the language, but its Windows GUI bindings are the least mature
  part of the ecosystem — which is exactly the platform we care most about. Being an early
  adopter on the critical path is the wrong risk to take.
- **Porting GTK4 to Windows.** Faster to a running binary, and it produces the
  lowest-common-denominator result the Ghostty team explicitly refused. If the goal were a
  terminal that runs on Windows, this would win; the goal is one that *belongs* there.
- **Writing our own VT parser.** The largest, longest, least differentiated part of the
  project. Not writing it is the single highest-leverage decision here.
- **Forking Ghostty for Windows.** Community forks exist and none is official. A fork
  inherits a Zig codebase plus the entire upstream merge burden, for a frontend upstream
  intends to write differently anyway.

## Success criteria (for when work starts)

Deliberately narrow, because "passes vttest" is not a real target — even xterm does not
pass it all:

1. `tmux`, `vim`, `htop` and `fzf` are fully usable on both Windows and Linux.
2. Correct column alignment for CJK and emoji, using our own grapheme/width tables rather
   than libc `wcwidth`.
3. Scrollback reflows correctly on resize, including across the alternate screen.
4. IME composition works on both platforms — treated as a launch requirement, not a
   follow-up, because retrofitting it is a rewrite of the input path.
5. **Usable with a screen reader on Windows** (Narrator/NVDA): a UI Automation provider for
   the terminal content and the tab strip. winit supplies none of this. Also a launch
   requirement, and the item most likely to be quietly dropped, which is why it is numbered
   rather than mentioned.
6. Frame pacing holds under `cat` of a large file without tearing or unbounded latency, and
   meets the measured triggers in the answered-questions section above.
7. One core library, two frontends, and no platform `#[cfg]` inside the core.
8. The VT core sits behind our own trait from the first commit, with `alacritty_terminal` as
   the initial implementation — verified by a compiling second implementation stub, not by
   intention.

## Open questions — answered (2026-08-31)

These were deferred pending evidence. The evidence is now in
[`docs/terminal-research.md` §9](../terminal-research.md), with sources. Summary and effect:

**Is `libghostty-vt`'s C ABI ready?** **No, and it says so itself.** `include/ghostty/vt.h`
carries an explicit warning that the API "is not yet stable and is definitely going to
change." There is no semver, no library-only release tag. The surface *is* real and
expanding fast — terminal state landed March 2026, and `ghostty-org/ghostty` ships
**Ghostling**, an official single-file C proof-of-concept consumer — but nobody has published
Rust bindings, and consuming a Zig-built static library from `build.rs` is unprototyped.

→ **Decision changed:** start on **`alacritty_terminal`** behind our own trait, not on
libghostty-vt. It is published on crates.io, pure Rust, proven on Windows/ConPTY, and already
embedded by Zed and others. The trait must exist *before* any rendering code, so the swap is a
swap rather than a rewrite. The protocol gaps that motivate eventually swapping are concrete:
`alacritty_terminal` has no kitty graphics, no kitty keyboard, no sixel, and only very recent
OSC 133.

→ **Added deliverable:** prototype a `libghostty-vt-sys` crate early, as a spike. "The swap
will be straightforward" is currently an assumption, and it is cheap to test and expensive to
be wrong about.

**Is wgpu fast enough, or is a D3D11 fast path needed?** **Good enough to start.** The
decisive finding is that Windows Terminal's AtlasEngine gets its speed from the **glyph atlas
and quad-batching design, not from D3D11** — PR #13477 doubled its frame rate by optimizing
glyph generation. That pattern is API-agnostic and implementable on wgpu. Rio ships on wgpu
with tabs and splits. A published benchmark also shows Ghostty at *worse* input latency than
Alacritty despite both being GPU-accelerated, which says frame pacing and the input-to-render
path dominate the rendering API.

→ **Decision confirmed.** With an explicit trigger rather than a vague intention: investigate
a D3D11 path only if `cat` of a 10 MB file exceeds **500 ms** or input latency exceeds
**20 ms**, *and* profiling attributes it to wgpu submission/validation rather than glyph
rasterization. Could not find wgpu-vs-D3D11 microbenchmarks for 2D text specifically; that
gap is why the trigger is a measurement rather than a prediction.

**Windows chrome: winit-drawn, WinUI 3, or Win32?** **Draw it ourselves in the winit window**,
the WezTerm model. WinUI 3 would mean a second frontend codebase — defeating the core/frontend
split that is the reason for the whole architecture — plus a WinAppSDK runtime dependency and
no Linux story. Windows Terminal took WinUI 3 and got native tabs, Mica, snap layouts and
accessibility for free, at the cost of being C++/XAML-only.

→ **Decision confirmed, with costs now itemized.** winit gives per-monitor-v2 DPI and IME for
free. It does **not** give: snap layouts (needs `WM_NCHITTEST` returning `HTMAXBUTTON`; winit
issue #3884 is open), Mica/acrylic (needs `DwmSetWindowAttribute` on the raw HWND), caption
buttons, or **any** UI Automation.

→ **Decision added — accessibility is a launch requirement, not a follow-up.** winit provides
zero UIA support, and both WezTerm and Alacritty have poor Windows accessibility. A terminal
that claims to *belong* on Windows and is unusable with a screen reader does not belong on
Windows. This is the single largest under-budgeted work item in the plan.

**ConPTY passthrough — has it shipped?** **No.** `passthroughMode` (PR #11264) is still
Dev-build-only and experimental; a maintainer confirmed it is "limited strictly to Dev builds
(not even Preview)". Issues #1173 and #1985 remain open. The successor design, **in-process
ConPTY** (spec #13000, dated 2024-06-07), would remove the VT translation layer entirely — but
it is a spec with no verifiable implementation PRs, and looks like a multi-year effort.

→ **Decision confirmed and hardened:** there is no true passthrough PTY on Windows today, and
none is coming soon. Every third-party terminal lives with ConPTY's double interpretation. The
ceiling on Windows fidelity is upstream of us and will be documented as such rather than
absorbed as our own bug.

## Remaining open questions

- Can a Zig-built `libghostty-vt` static library be linked from a Rust `build.rs` on MSVC?
  **Unverified — nobody has published one.** This is the spike above.
- What is Rio's actual input latency on Windows? No published numbers were found; the wgpu
  recommendation rests on architectural reasoning plus Rio shipping, not on a measurement.
- How much UIA surface does a terminal actually need to be usable with Narrator/NVDA — the
  full text-pattern provider, or a narrower subset?
