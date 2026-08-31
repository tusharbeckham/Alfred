# Terminal research — what Ghostty actually is, and what a Windows+Linux-native terminal costs

> **Status:** research only (2026-08-31). Nothing is being built yet. This is the
> reading behind [ADR 002](adr/002-native-windows-linux-terminal.md), which records the
> decision. Every external claim carries a source URL; where a claim could not be
> verified, it says so.

The Owner's brief: Ghostty is the inspiration, but Ghostty is macOS- and Linux-native
and we want **Windows-native and Linux-native**. So the two questions worth answering
before a line of code exists are *what makes Ghostty good* and *why Windows is the hard
part*.

---

## 1. Ghostty's architecture

| Layer | Choice |
|---|---|
| Language | **Zig**, compiled to native code |
| Core | **`libghostty`** — the terminal emulation, font handling and rendering, exposed over a **C ABI** so any frontend can embed it |
| macOS frontend | Swift, **AppKit + SwiftUI**, linking the C API |
| Linux frontend | Zig against the **GTK4** C API, optionally libadwaita |
| Renderer | **Metal** on macOS, **OpenGL** on Linux |
| Fonts | Linux: fontconfig + FreeType + HarfBuzz · macOS: CoreText + HarfBuzz |
| Event loop | **libxev**, extracted from Ghostty as its own library |

Sources: [ghostty.org/docs/about](https://ghostty.org/docs/about),
[mitchellh.com/writing/ghostty-devlog-001](https://mitchellh.com/writing/ghostty-devlog-001)

The structurally important decision is the **core/frontend split**. Ghostty is not a
GTK app with a terminal in it; it is a terminal *library* with two separate native
frontends. That is what makes a third frontend a tractable project rather than a
rewrite — and it is the single idea most worth copying, independently of any language
choice.

The VT parser is being extracted further as **`libghostty-vt`**: a parser and terminal
state machine (cursor, styles, wrapping) with **zero dependencies — not even libc** —
which already **supports Windows as a build target**.
Source: [mitchellh.com/writing/libghostty-is-coming](https://mitchellh.com/writing/libghostty-is-coming)

That last detail matters more than anything else in this document. The hardest,
longest, least glamorous part of a terminal emulator is being handed out as a portable
library by the project we are taking inspiration from.

---

## 2. Why Ghostty has no Windows support

This is the claim most worth getting right rather than guessing at, so it is quoted.

Mitchell Hashimoto, opening the Windows tracking discussion (#2563):

> "This is a tracking issue for windows support. I'm not yet committed on Windows
> working for Ghostty 1.0 but I wanted to make this issue in case anyone wants to help."

The devlog confirms the codebase was written "in a way that Windows support will come
later." The roadmap he sketched is incremental: GLFW + OpenGL + FreeType compiling
first, then font discovery through DirectWrite, then platform-native rasterization,
then replacing OpenGL with DirectX, then a dedicated Windows `apprt`, then installers.

As of April 2026 a collaborator (@pluiedev) stated the team's position: they want a
**native Windows UI rather than GTK ported to Windows**, a **Direct3D renderer**,
Windows 10/11 only, and minimal C++. The frontend framework is still undecided between
WinUI 3, WPF and pure Win32. Community forks exist; none is official.

Sources: [discussions/2563](https://github.com/ghostty-org/ghostty/discussions/2563),
[devlog-001](https://mitchellh.com/writing/ghostty-devlog-001)

**Conclusion, stated carefully:** no fundamental technical blocker was cited. The
reason is prioritization and manpower, plus an explicit refusal to ship a
lowest-common-denominator port. That is a *product* judgement, and it happens to be the
same judgement the Owner is making — "Windows-native, not Windows-tolerated."

---

## 3. What makes Ghostty notable, and what is actually hard about each

- **Terminfo / xterm correctness.** Implements the full DEC ANSI state diagram. Mitchell
  reports *still finding edge cases after 3+ years*. Hard because the "spec" is decades
  of de facto behaviour scattered across implementations, not one document.
- **Kitty graphics protocol** (inline images). Hard because image lifecycle, placement,
  z-order against text and GPU texture management all have to stay correct *while the
  buffer scrolls*.
- **Kitty keyboard protocol** (disambiguated keys, key-up events). Hard because every OS
  reports keyboard input differently — and on Windows, ConPTY backs input with VT
  sequences, which limits the key range that can be expressed at all.
  Source: [microsoft/terminal#4999](https://github.com/microsoft/terminal/blob/main/doc/specs/%234999%20-%20Improved%20keyboard%20handling%20in%20Conpty.md)
- **OSC 133 semantic prompts + shell integration.** Ghostty auto-injects integration
  scripts so resize reflows correctly and new tabs inherit the working directory. Hard
  because each shell needs a different injection mechanism.
- **Performance.** GPU rendering plus a SIMD parser. Hard to benchmark honestly —
  startup time, IO throughput and frame rate are three different claims.

---

## 4. The Windows problem: ConPTY

**What it is.** The Windows Pseudo Console, added around 2018, replacing the legacy
console API so a terminal can spawn console programs and talk VT to them.
Source: [devblogs.microsoft.com — Introducing the Windows Pseudo Console](https://devblogs.microsoft.com/commandline/windows-command-line-introducing-the-windows-pseudo-console-conpty/)

**Why it is not a Unix PTY.** Conhost *interprets* the incoming VT stream, maintains its
own screen buffer, and then **regenerates** a VT stream for the terminal. Everything
passes through two interpretations. That loses information, reorders operations, and
truncates sequences conhost does not recognise — so a terminal on Windows is not
reading what the program wrote, it is reading conhost's re-rendering of what conhost
understood.

Consequences, each independently reported:

| Problem | Evidence |
|---|---|
| Escape sequences lost or altered in the round trip | [terminal#4116](https://github.com/microsoft/terminal/issues/4116) |
| Passthrough mode requested 2019, merged as PR #17510, still has buffer-trashing and ordering problems on enter/exit | [terminal#1173](https://github.com/microsoft/terminal/issues/1173), [terminal#8698](https://github.com/microsoft/terminal/issues/8698) |
| Resize/reflow artifacts; sequences split across writes render incorrectly | [terminal#4037](https://github.com/microsoft/terminal/issues/4037) |
| Keyboard input limited by being VT-backed | [terminal#4999](https://github.com/microsoft/terminal/blob/main/doc/specs/%234999%20-%20Improved%20keyboard%20handling%20in%20Conpty.md) |
| Added latency from the conhost intermediary | inherent to the architecture above |

**winpty** is the pre-ConPTY shim (old Git Bash, mintty). Effectively superseded;
relevant only as a fallback story for Windows versions we will not support anyway.

**How others cope:** WezTerm and Alacritty both use ConPTY and accept its limits
pragmatically. Windows Terminal is built by the team that owns ConPTY and still hits the
same buffer-regeneration architecture.

**The honest conclusion:** on Windows, the ceiling on terminal correctness is not set by
our emulator. It is set by ConPTY. Any plan that promises Ghostty-grade fidelity on
Windows without saying that out loud is overpromising.

---

## 5. Prior art worth learning from

| Terminal | Language | GPU | PTY layer | Lesson |
|---|---|---|---|---|
| [WezTerm](https://github.com/wezterm/wezterm) | Rust | OpenGL | own `portable-pty` (Unix PTY + ConPTY) | One Rust codebase genuinely ships native on Windows, Linux and macOS. Existence proof for the whole plan. |
| [Alacritty](https://github.com/alacritty/alacritty) | Rust | OpenGL via glutin/winit | `alacritty_terminal` crate | Ruthless simplicity — no tabs, no splits — kept it fast and portable. `winit` is proven adequate for a terminal. |
| [Windows Terminal](https://github.com/microsoft/terminal) | C++/WinUI | **Direct3D 11 "AtlasEngine"** | owns ConPTY | AtlasEngine exploits the monospace-grid assumption for large GPU wins. The reference for DirectWrite text rendering on Windows. |
| Kitty | C + Python | OpenGL | Unix PTY only | Sets the standard for protocol extensions — and shows the cost of never targeting Windows. |
| Zed's terminal | Rust | Blade/wgpu | embeds Alacritty's crate | You do not have to write the VT core. Embedding is a legitimate strategy. |
| [Rio](https://github.com/raphamorim/rio) | Rust | **wgpu** | `portable-pty` | One GPU API across Vulkan/D3D12/Metal is achievable; wgpu's overhead is non-trivial for a terminal and needs measuring, not assuming. |

Two of these (Zed, Rio) independently confirm the two decisions that matter most:
**embed a VT core rather than writing one**, and **abstract the GPU once**.

---

## 6. Technology options

**Language.** Rust has the ecosystem (winit, wgpu, portable-pty, cosmic-text) and three
shipping terminals as proof. Zig is what Ghostty proves out beautifully, but its Windows
GUI bindings are younger and we would be early adopters on the exact platform we care
most about. C++ is what Windows Terminal proves, at a permanent cost in developer
experience and memory safety.

**GPU.** wgpu maps to D3D12 on Windows and Vulkan on Linux from one codebase, which
removes the single largest source of duplicated work. Straight D3D11 is what AtlasEngine
uses and is faster on Windows, at the price of two renderers. OpenGL works everywhere
and is deprecated on macOS with uneven Windows drivers.

**Windowing.** winit gives cross-platform windows and input but no native *widgets* —
tabs and menus would be drawn by us, which is what Alacritty does. Native Win32 + GTK4
is Ghostty's answer: maximum native feel, two entire frontend codebases.

**Fonts.** DirectWrite on Windows is effectively non-negotiable if the goal is "native",
because ClearType-quality rendering is what Windows users compare against.
FreeType+HarfBuzz on Linux is the standard. A pure-Rust stack (swash/cosmic-text) is
portable and less mature, and will not match DirectWrite's hinting on Windows.

### Recommended stack

**Rust + wgpu + winit + `libghostty-vt` for the VT core + DirectWrite (Windows) /
FreeType+HarfBuzz (Linux) for fonts.**

The reasoning, in one line each: Rust for ecosystem and three existence proofs; wgpu
because one GPU backend instead of two is the biggest single saving available;
`libghostty-vt` because terminal emulation is a multi-year correctness problem that
someone is handing us as a zero-dependency library that already builds for Windows; and
a **platform-specific font stack because that is precisely where "native" is felt** —
abstracting fonts would save code and lose the thing the project is for.

The accepted trade-off is that winit provides no native widgets, so tabs and the command
palette are ours to draw.

---

## 7. The hard parts nobody budgets for

- **Grapheme clustering and `wcwidth`.** Unicode grapheme boundaries change every Unicode
  release and libc `wcwidth` tables are perpetually stale. Get it wrong and column
  alignment breaks for every CJK and emoji user — which is most users, since emoji.
- **Scrollback reflow on resize.** Re-wrapping history on a width change, while
  interacting with the alternate screen, semantic prompt regions and live selection.
  Most terminals get this subtly wrong.
- **IME.** CJK input needs a composition region with a candidate window positioned at the
  cursor — `ImmSetCompositionWindow`/TSF on Windows, IBus/Fcitx on Linux. Getting it
  wrong makes the terminal unusable for over a billion people, and it is invisible to a
  developer who only types ASCII.
- **Ligatures.** Shaping runs across cells, then splitting back into a cell grid while
  preserving advance widths, cursor position and selection.
- **Images.** Sixel (legacy DEC) and the Kitty protocol: GPU texture lifetime, z-order
  against text, scrolling, and garbage-collecting images that scrolled away.
- **Damage tracking.** Redrawing only changed cells is what makes it fast; it interacts
  with scrolling, selection highlight and cursor blink.
- **Frame pacing.** Too fast wastes GPU and tears, too slow feels laggy. Variable refresh
  rate displays, plus batching bursty IO into single frames. OSC 2026 (synchronized
  output) adds a protocol-level frame gate.
- **Conformance.** `vttest` and `esctest` test hundreds of obscure DEC behaviours. Full
  passing is a multi-year effort that *even xterm does not achieve*. A plan should state
  a target subset rather than imply completeness.

---

## 8. What this means for scope

The research changes the shape of the project. Written down plainly:

1. **Do not write a VT parser.** `libghostty-vt` is zero-dependency, fuzz-tested, C-ABI,
   and already targets Windows. Writing our own would be the largest and least
   differentiated part of the work.
2. **Copy the core/frontend split from day one**, whatever the language. It is what makes
   a second platform an addition rather than a fork.
3. **Say out loud that ConPTY caps Windows fidelity.** It is not our bug and it cannot be
   fixed from our side of the pipe.
4. **`wcwidth`, reflow and IME are the schedule risks**, not rendering. Rendering is the
   part with the most prior art and the clearest reference implementation.
5. **Pick a conformance target explicitly.** "Passes vttest" is not a real goal; "passes
   the subset that tmux, vim, htop and fzf depend on" is.


---

## 9. The empirical questions, answered (2026-08-31)

Section 6 recommended a stack and §8 left four things unresolved. This section resolves them
with current sources. Where a claim could not be verified it says so — a labelled gap is more
useful than a confident guess.

### 9.1 `libghostty-vt` is real, consumable, and explicitly unstable

The header states it outright:

> "This is an incomplete, work-in-progress API. It is not yet stable and is definitely going
> to change."
> — [`include/ghostty/vt.h`](https://github.com/ghostty-org/ghostty/blob/main/include/ghostty/vt.h)

What *is* true: the surface covers terminal state, render state, formatters, OSC/SGR parsers,
key/mouse/focus encoding, paste and Unicode utilities. Terminal state landed March 2026
([PR #11676](https://github.com/ghostty-org/ghostty/pull/11676), completed by
[#11814](https://github.com/ghostty-org/ghostty/pull/11814), tracked in
[discussion #11348](https://github.com/ghostty-org/ghostty/discussions/11348)). There is an
official proof-of-concept consumer — [**Ghostling**](https://github.com/ghostty-org/ghostling),
a minimal terminal in a single C file — which is the strongest available evidence that the API
is usable rather than aspirational.

There is **no semver and no library-only release tag**, so breakage should be expected.

Building the *full* libghostty on Windows hit a libxml2/symlink problem
([#11697](https://github.com/ghostty-org/ghostty/discussions/11697), addressed by
[#11698](https://github.com/ghostty-org/ghostty/pull/11698) by skipping fontconfig on
Windows); the `ghostty-vt` module alone is zero-dependency and should build cleanly.

**Could not verify:** that a Zig-built `libghostty-vt` static library can be linked from Rust
on MSVC. No `-sys` crate exists. This is now an explicit spike in ADR 002 rather than an
assumption inside it.

**The fallback is really the starting point.** `alacritty_terminal` is on crates.io (v0.25.0,
mid-2025), embedded by **Zed**, [iced_term](https://github.com/Harzu/iced_term) and originally
Rio, with solid ConPTY support. What it lacks against libghostty-vt is concrete: no kitty
graphics, no kitty keyboard ([alacritty#6378](https://github.com/alacritty/alacritty/issues/6378)),
no sixel (declined by maintainers), and OSC 133 only very recently
([alacritty#5860](https://github.com/alacritty/alacritty/pull/5860)). Those four gaps *are* the
reason to eventually swap — which is an argument for the trait, not for waiting.

### 9.2 wgpu is sufficient; the win is the atlas, not the API

The decisive evidence is what AtlasEngine actually does. Per its
[README](https://github.com/microsoft/terminal/blob/main/src/renderer/atlas/README.md) it keeps
a glyph cache with a per-glyph hashmap lookup, stages glyphs with `_appendQuad` and batches
with `_flushQuads` — a textured quad grid over a fixed-size cell. And the big measured win came
from **glyph generation**, not from the graphics API:
[PR #13477](https://github.com/microsoft/terminal/pull/13477) reports roughly a 100%
improvement (15→30 FPS), with [#13906](https://github.com/microsoft/terminal/pull/13906)
reaching 144 FPS during animation. That pattern is API-agnostic.

Cross-terminal latency figures from
[moktavizen/terminal-benchmark](https://github.com/moktavizen/terminal-benchmark) (Wayland):
Alacritty **16.7 ms** input latency and 404 ms for an 11 MB `cat`; WezTerm **30.8 ms** and
1246 ms; Ghostty **38.3 ms**. Ghostty being *slower* than Alacritty while both are
GPU-accelerated is the useful data point: frame pacing and the input-to-render path dominate
the choice of graphics API.

**Could not verify:** published Rio (wgpu) latency numbers, or any wgpu-vs-D3D11 microbenchmark
for 2D text. Hence the recommendation is paired with a **measurement trigger** rather than
presented as settled: revisit only if `cat` of 10 MB exceeds 500 ms or input latency exceeds
20 ms *and* the profile blames wgpu submission rather than rasterization.

### 9.3 Windows chrome: what winit gives, and what it does not

| Concern | winit | Must be built |
|---|---|---|
| Per-monitor-v2 DPI | **yes** | — |
| IME | **yes** (used by Alacritty) | candidate-window position tuning |
| Custom titlebar | undecorated window + hit-test | caption buttons, hover states, hit regions |
| Snap layouts (Win11) | **no** — needs `WM_NCHITTEST` → `HTMAXBUTTON`; [winit#3884](https://github.com/rust-windowing/winit/issues/3884) open | raw HWND hook |
| Mica / acrylic | **no** | `DwmSetWindowAttribute` on the raw HWND |
| Dark mode | partial (title text colour) | everything, once chrome is custom-drawn |
| **UI Automation** | **none at all** | a full provider |

Windows Terminal chose WinUI 3 and got native tabs, Mica, snap layouts, accessibility and IME
for free — at the price of C++/XAML, a WinAppSDK runtime, and no cross-platform story.
[WezTerm](https://github.com/wez/wezterm) draws everything itself
([wezterm#1180](https://github.com/wez/wezterm/issues/1180)) and ships on three platforms from
one codebase, at the price of chrome that looks native nowhere and limited accessibility.

The second model is the right trade for this project — but **accessibility is the item to
budget properly.** It is the one thing WinUI 3 would have given for free that genuinely
matters, and both Rust terminals are weak at it.

### 9.4 ConPTY passthrough has not shipped

`passthroughMode` arrived as an FHL project
([PR #11264](https://github.com/microsoft/terminal/pull/11264)) and a maintainer confirmed in
[discussion #13133](https://github.com/microsoft/terminal/discussions/13133) that it is
"limited strictly to Dev builds (not even Preview)". Issues
[#1173](https://github.com/microsoft/terminal/issues/1173),
[#1985](https://github.com/microsoft/terminal/issues/1985) and
[#8698](https://github.com/microsoft/terminal/issues/8698) (ordering not maintained) are open.

The successor is **in-process ConPTY**
([spec #13000](https://github.com/microsoft/terminal/blob/main/doc/specs/%2313000%20-%20In-process%20ConPTY.md),
2024-06-07), which would delete the VT translation layer entirely. **Could not verify** any
merged implementation PRs; it reads as a multi-year effort.

**Conclusion unchanged from §4, now with a date on it:** there is no true passthrough PTY on
Windows, and none is imminent. Plan around ConPTY's double interpretation rather than for its
removal.

### 9.5 Net effect on the plan

| Question | Verdict |
|---|---|
| Rust + core/frontend split | confirmed |
| wgpu | confirmed, with a measurement trigger |
| winit-drawn chrome | confirmed, with itemized costs |
| ConPTY caps Windows fidelity | confirmed |
| `libghostty-vt` as day-one VT core | **changed** — start on `alacritty_terminal` behind a trait |
| Rust FFI spike for libghostty-vt | **added** as an early deliverable |
| Accessibility (UIA) | **added** as a launch requirement |
| Snap layouts | **added** as a known raw-HWND work item |
