# Mupen64-rr-lua file formats, release by release

What changed in `.m64` movies and `.st` savestates across every mupen64-rr-lua release (tags 1.0.0 … 1.5.0-6),
which changes stop an older Mupen from opening a newer file, and the evidence. Built 2026-10-15 for Mupen Compat.

**Sources.** The savestate and VCR code of all 71 release tags (mupen64/mupen64-rr-lua). **Tests.** The real release binaries, on a hidden
desktop (results below; that test harness depends on the author's machine and is not in this repository).

## The short version
| From | What it writes | Who can't read it | Fix (what the tool does) |
|---|---|---|---|
| **1.5.0+** | savestates as **LZ4 frames** (option "st_lz4", on by default) | **everything before 1.5.0**: they take the LZ4 bytes for an uncompressed state, read the "ROM MD5" from them and say the state is from another ROM (1.1.x: "Error", 1.2+: "Savestate … another rom" Yes/No) | recompress as gzip; the contents are byte-identical |
| **1.5.0+** | movies with **extended header version 4** (`0x16`) | **1.1.9 – 1.4.0-8**: "VCR Error 12: The movie's extended version is invalid" | set the version to 1 and clear the v2–v4 fields (flags bits 1–2, `0x30–0x43`); Wii VC flag kept |
| a future Mupen | extended version 5+ | **1.5.0 – 1.5.0-2** reject it (1.5.0-3+ only warn) | lower to 4 |
| 1.4.0-beta+ | an optional state **screenshot**, 4 bytes a pixel | 1.1.8 – 1.3.0-20 read 3 bytes a pixel (the state still loads; tested) | drop the section (the picture redraws on the next frame) |
| 1.1.8 – 1.3.0-20 | an optional screenshot, 3 bytes a pixel | 1.4.0-beta+ skip it (not enough bytes) | drop it |

Nothing else blocks an older version: the state body (registers, RDRAM, TLB …, a fixed 0xA02BB4 bytes), the
interrupt queue, the movie block inside states and the 1024-byte movie header are the same from 1.0.0 to 1.5.0-6.

**The published 1.4.0-8 binary is not the 1.4.0-8 source.** The tag's source has LZ4 states (#926, 2026-08-08)
and the v4 header (#927, 08-09), but the release asset (`mupen64-w32`, uploaded 08-11) behaves like 1.4.0-7 in
every test: it rejects v4 movies and LZ4 states, still has the CLI bug #921 fixed on 08-08 ("VCR Error 2" when
starting with `--rom`), and its config has no `st_lz4` or RCP-lag keys. The tool follows the binaries: 1.5.0 is the
first release with both.

## Savestates (`.st`, `.savestate`)
Layout (every version): ROM MD5 as 32 ASCII hex chars; the registers; RDRAM 8 MB at `0x1B0` (little-endian words);
SP DMEM/IMEM; PIF RAM; flash RAM info (24 at `0x8021F0`); TLB lookup tables (1 MB each); `llbit`; GPRs; COP0 as 32
slots of 8 bytes at `0xA0230C` (the loader reads the low 4); lo, hi; FPRs; FCR0/31; TLB entries; PC; next
interrupt, next VI, VI field: 0xA02BB4 bytes. Then the interrupt queue ((type, count) pairs ending in
`0xFFFFFFFF`, at most 1 KB), a u32 "movie active", and if set the movie block (size, uid, current sample, current VI,
length, then length+1 inputs). Then, since 1.1.8, optionally `"SCR\0"`, width, height and the pixels.

| Version | Change | Effect on older readers |
|---|---|---|
| 1.0.0 | gzip (zlib `gzopen`: reads plain files too) | |
| 1.0.3 | "old st" detection (PC = 0x80000180) on load | none |
| 1.0.9 | snapshot movies also find `NAME.savestate` | naming only |
| 1.0.10 | a state saved during an input poll finishes the SI DMA and sets bit 31 of the RDRAM manufacturer register (`0x44`); the loader clears it and skips the DMA's interrupt | 1.0.0–1.0.9 keep the bit and may run the poll twice; the tool clears the bit for them. 1.2.0+ no longer set it (they still read it) |
| 1.1.1 | reads the whole state, then checks it before applying | none |
| 1.1.6 | libdeflate (gzip or plain) | none |
| 1.1.8 | movie block written field by field (same bytes); screenshot section | trailing data is ignored by older loaders |
| 1.1.9-3 | undo-load buffer (in memory only) | none |
| 1.4.0-beta-b4f8f172 | screenshot 3 → 4 bytes a pixel | see above |
| 1.4.0-8 (source) | the loader rejects states whose SI DMA address or flash RAM pointers are out of range | old → new only |
| **1.5.0 (release)** | **LZ4 frames**, chosen by the first bytes (`1F 8B` gzip, `04 22 4D 18` LZ4, else plain) | see above |
| 1.5.0-6 | the COP0 slots' high halves are zero (before: the next register's value, a read past the 32-bit array) | none; the tool uses it to date a state |

How the tool dates a state (the "Made by" column): LZ4 → 1.5.0+; RGBA screenshot → 1.4.0-beta+; RGB screenshot →
1.1.8 – 1.3.0-20; the manufacturer bit → 1.0.10 – 1.1.9-8; COP0 high halves zero → 1.5.0-6+ (or a development build with that change); COP0 high halves = next register → up to 1.5.0-5.

## Movies (`.m64`)
The 1024-byte header has been the same struct since 1.0.0; versions 1–3 are read by all (3 = the current one).
| Version | Change | Effect |
|---|---|---|
| 1.0.6 | start from EEPROM (start flags bit 2) | 1.0.0–1.0.5 can't play those (the tool refuses) |
| 1.0.0 – 1.0.8 | snapshot movie `a.b.c.m64` loads `a.st` only | |
| 1.0.9 – 1.1.7 | `a.st`, then `a.savestate` | |
| 1.1.8 | `a.st`, `a.savestate`, `a.b.st`, `a.b.savestate`, `a.b.c.st` … (shortest first) | the tool names the state `a.st` for targets before 1.1.8 |
| 1.1.9 | extended header: `0x16` version (1), `0x17` flags (bit 0 Wii VC), `0x24` data (tag "MUPN", bruteforce word, rerecords high word). **Rejects extended version > 1.** Warns on version 0 with non-zero flags; asks on a Wii VC mismatch | before 1.1.9 these bytes were reserved and ignored: a v4 movie plays in 1.1.8-2 (tested) |
| 1.3.0 | c.eq.s accuracy option (not stored in movies yet) | |
| 1.4.0-8 (source), **1.5.0 (release)** | extended version 4: flags bit 1 accurate c.eq.s, bit 2 accurate RDP completion; `0x30` double CPU counter factor, `0x38` double RCP lag factor; sync warnings; rejects > 4 | 1.1.9 – 1.4.0-8 reject it |
| 1.5.0-3 | an unknown extended version only warns ("from a newer version of Mupen64") | |

"Extended Movie Format" off (1.1.9+) is meant to write version 0, but `write_movie_impl` zeroes one byte of the data
section (`memset(&extended_data, 0, sizeof(extended_flags))`), so the tag "MUPN" survives: such
movies are dated "1.1.9 or newer with Extended movie format off".

**Plugins.** The header's four plugin names (`0x122` video, `0x162` audio, `0x1A2` input, `0x1E2` RSP) are a record
only: no version loads or switches plugins for a movie. 1.0.0 – 1.1.6 print a console warning when they differ from
the selected plugins (never shown in the window); 1.1.7+ don't compare them at all. The tool leaves them as they are.
What does matter: (1) each Mupen era needs its own plugin DLLs (tested: 1.3.0-11 refuses the 1.4-era TAS plugins with
"Choose Default Plugins"; 1.5.0+ has its plugins inside the exe, recorded as "TAS Video (built-in)" etc., so there is
nothing to copy to an older version; 1.0.10 recognised none of the plugins here); (2) plugins that change emulation
timing (the RSP most of all) can desync a movie. For SM64 the 1.5 built-in-plugin movie synced with the 1.3-era
TASVideo/TASRSP and no audio plugin in every tested version. "(built-in)" names also date a movie to 1.5.0+, even after
its header version was lowered.

**Sync settings the tool reports when lowering the version** (the old format can't carry them): Wii VC (option since
1.1.9), c.eq.s accuracy (option since 1.3.0), counter factor (option in every version), RCP lag and RDP completion
(no emulation before 1.5.0). A movie recorded with them may desync where the setting can't be matched.

## Test results (2026-10-15)
Cases: the 675f secret-reds movie (Island ROM, 683 inputs, extended v4) + its LZ4 `secretarea.st`, made in 1.5;
"syncs" = played to the last input with gGlobalTimer, Mario's action and position identical to the 1.5.0-5 reference
on every game frame. "v4 movie alone" = the original movie with a gzip copy of the state. LZ4 state = a 1.5 savestate on the same ROM.
| Version | 1.5 pair, original | 1.5 pair, converted | v4 movie alone | LZ4 state, original | LZ4 state, converted |
|---|---|---|---|---|---|
| 1.1.8-2 | fails (state "from another rom") | syncs | **syncs** | fails | loads |
| 1.1.9-8 | fails (extended version invalid) | syncs | fails (extended version invalid) | fails | loads |
| 1.2.0 | fails (extended version invalid) | syncs | | fails | loads |
| 1.3.0-7, -11, -13, -15, -17, -19 | fails (extended version invalid) | syncs | | fails | loads |
| 1.4.0-beta-90e2204a | fails (extended version invalid) | syncs | | fails | loads |
| 1.4.0-7 | fails (extended version invalid) | syncs | | fails | loads |
| 1.4.0-8 (release) | fails (extended version invalid) | syncs | fails (extended version invalid) | fails | loads |
| 1.5.0, 1.5.0-2 | syncs (after the RCP-lag sync question) | syncs | syncs (1.5.0) | loads | loads |
| 1.5.0-6 | syncs | syncs | | loads | loads |

Also: an extended-v5 copy of the movie fails in 1.5.0-2 ("might be too new") and syncs there converted; 1.5.0-6 plays
it with a warning. A gzip state with an RGBA screenshot loads in 1.3.0-11, 1.4.0-beta and 1.5.0-6, converted or not.
### Multi-movie sync, both directions (2026-10-15)
"Syncs" = every game frame identical to the reference (gGlobalTimer, Mario's action and position; for the JP ROM the
same RAM words at every input sample) up to the last input. Runs in silent mode (no dialogs, no beeps).
| Movie | ROM | Start | Inputs | Format | Reference | Played in | Result |
|---|---|---|---|---|---|---|---|
| Task 11 (TAS comp) | US | savestate (LZ4) | 783 | ext 4 | 1.5.0-6 | converted: 1.4.0-7, 1.3.0-11, 1.1.9-8 | all sync |
| Decade comp task 1 | US | savestate (LZ4) | 1,249 | ext 4 | 1.5.0-6 | converted: 1.4.0-7, 1.3.0-11, 1.1.9-8 | all sync |
| Bowser stage | Island | savestate (LZ4) | 1,604 | ext 4 | 1.5.0-6 | converted: 1.4.0-7, 1.3.0-11, 1.1.9-8 | all sync |
| poweron.lobby.168f | Island | power-on | 168 | ext 4 | 1.5.0-6 | converted: 1.4.0-7, 1.3.0-11, 1.1.9-8 | all sync |
| poweron.room1_to_sk100c.13209f (several levels) | Island | power-on | 13,209 | ext 4 | 1.5.0-6 | converted: 1.3.0-11 | syncs |
| wdwexpress | US speed patch | savestate | 154 | ext 1 (1.4.0-7 plugins) | 1.4.0-7 | 1.5.0-6 | syncs |
| bits_exit.to_rr | US | savestate | 133 | ext 1 (1.3 plugins) | 1.3.0-11 | 1.5.0-6 | syncs |
| task 9 star road | Star Road | savestate | 751 | ext 1 (1.4 beta plugins) | 1.4.0-beta | 1.5.0-6 | syncs |
| sk_6.finaltrip | Island | savestate | 2,728 | ext 1 | 1.4.0-7 | 1.5.0-6, 1.3.0-11 | both sync |
| Castle Grounds to Lobby 5.20 (old HLE RSP plugin era) | JP | power-on | 1,770 | ext 0 | 1.3.0-11 | 1.5.0-6, 1.1.9-8 | both sync |
| hole_in_two | US speed patch | power-on | 2,614 | ext 1 | 1.3.0-11 | 1.5.0-6 | syncs |

So for SM64 and its US-based hacks, the versions from 1.1.9 to 1.5.0-6 emulate identically, and the file format is
the only thing standing between them. 1.1.8-2 couldn't serve as a reference for a power-on movie: its Lua logger
wrote nothing (a power-on start restarts `--lua` scripts), so that one is not a sync result.

**Real input (2026-10-15):** genuine mouse drags from Explorer (one file, then two
selected with Ctrl+A onto the drop area, the duplicate ignored and the pair recognised) and a real click on Convert,; the copies landed in "for every Mupen".

**Not tested:** 1.0.x (1.0.10 found none of the available plugins; its rules come from the source only) and the
1.1.1 – 1.1.7 releases (no binaries here).
