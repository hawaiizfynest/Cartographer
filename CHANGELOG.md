# Changelog

## v1.1.0

- Added an experimental test for fast block writing. The current ROM write is
  reliable but slow because it programs one word at a time over the cable. The
  device firmware also has a block-write command that programs a whole 256-byte
  block on the cart at once, which would be far faster. Whether that command
  drives a given flash chip correctly depends on the chip, so this is added as a
  test first: Tools > Test fast block write erases sector 0, writes one test
  block with the fast command, reads it back, and reports whether it landed
  correctly (including detecting the data-line-swapped case some repro carts
  need). It only touches sector 0 and needs a typed confirmation. If the test
  passes on a cart, a full fast-write mode can be built on the same command; if
  it does not, the reliable word-at-a-time write is there unchanged.

## v1.0.15

- ROM writes are meaningfully faster. About half the write time was a fixed
  settle pause after every command, left conservative from the chip-identify
  code where a missed command matters. During a write the app already confirms
  each word landed by reading it back, so that pause can be much shorter with the
  read-back and retry as the safety net. Writing now uses a shorter settle during
  the program loop, cutting a large chunk off the total time, while identify keeps
  the cautious timing it needs. If a word ever does not take at the faster pace,
  the existing retry catches it, so reliability is unchanged.

## v1.0.14

- Fixed ROM writes failing partway with a verify error. The program step sent
  each word and moved straight to the next without waiting for the chip to finish
  committing it. Most words kept up, but every so often one had not finished
  before the next command arrived and was silently dropped, so the write stopped
  at a verify mismatch. Programming now waits for each word to actually take
  (reading the address back until it matches, with a short timeout) before moving
  on, matching how the chip expects to be driven. This makes writes reliable at
  the cost of being a little slower, which on this word-at-a-time path is not
  noticeable.

## v1.0.13

- Fixed the Write ROM button overlapping the voltage-switch note at the bottom
  of the window. The button now sits on its own row across the full width, with
  the note below it.

## v1.0.12

- ROM writing has arrived for GBA flash carts. The app can now write a .gba ROM
  to a supported flash cart, built on the sector erase and verification pieces
  added in the last few releases. Writing walks the chip's sector map from CFI,
  erases and verifies each sector, programs it a word at a time using the
  standard unlock-and-program sequence, then reads the whole sector back and
  confirms it matches the file before moving on. If any sector fails to erase,
  program, or verify, the write stops right there and reports exactly where, so a
  bad write can never masquerade as a good one.
- Writing is word-at-a-time and therefore slow on this class of cart and older
  firmware, the same speed the reference flasher reports. That is a limitation of
  the hardware, not a setting to change. The write is safe to cancel: a cart with
  a partial ROM is not damaged and can be rewritten.
- This is exposed as Tools > Write ROM to flash cart, gated behind a clear
  warning and a typed confirmation, since writing erases whatever is currently on
  the cart.

## v1.0.11

- Groundwork for ROM writing: the app can now erase a single flash sector and
  verify it. This is the core building block a full ROM write is made of, added
  on its own so it can be tested in isolation before any write loop is built on
  top of it. Erasing a sector sends the standard unlock-and-erase sequence, polls
  the chip's status register until the erase finishes (with a timeout so it can
  never hang), then reads the sector back and confirms every byte is 0xFF. It
  targets one sector at a time against the sector map read from CFI, and reports
  exactly what the chip returned, so a failure is informative rather than silent.
  Erasing flash is destructive by nature; this step is deliberately small and
  self-checking so the write path can be built on a foundation that is known to
  work.

## v1.0.10

- Identify flash chip now reads the chip's true size and capabilities from its
  Common Flash Interface data, instead of guessing from the device ID. The
  S29GL-family carts all share device id 0x227E whether they are 16, 32, or 64
  MB, so the ID alone can not tell them apart. The probe now parses the CFI table
  it already reads and reports the real capacity, the sector layout, and whether
  the chip supports sector erase, chip erase and buffered writes. This is
  read-only and lays the groundwork for writing ROMs safely later: a correct
  erase needs the exact sector map, and now the app can read it.

## v1.0.9

- The flash chip identifier is more reliable on the finicky 5V repro carts. These
  clone carts can answer a probe on one run and stay silent on the next depending
  on what state they were left in. The probe now settles the bus after each
  command the way the reference flasher does, clears the chip out of any stuck
  mode before it starts, and retries the whole identification a few times before
  giving up. A cart that reads intermittently should now identify on one of the
  attempts rather than at random.

## v1.0.8

- Identify flash chip now reads a clean, complete chip ID on the repro carts it
  used to only half-read. The previous probe woke the chip but read the ID from
  slightly the wrong place, so a real S29GL-class cart came back with a partial
  ID and landed as "not in the database." The probe now runs a proper Common
  Flash Interface query the way the reference flasher does: it looks for the
  chip's "QRY" signature, and when it finds it, reads the manufacturer and device
  ID from the right offsets. Carts that reported a garbled ID now identify
  correctly.
- The probe reports whether it confirmed the chip through CFI, so a solid
  identification and a guess are easy to tell apart.

## v1.0.7

- The flash chip identifier now works on the repro carts that need 5V. Some
  Game Boy Advance flash carts, including many EpicJoy-style RTC and solar carts,
  ignore every flash command at 3.3V and only answer at 5V. Identify flash chip
  now forces 5V for the probe and tries the wider set of unlock addresses those
  chips actually use, so carts that used to read as "mask ROM" now identify
  correctly. The higher voltage is applied only for the identification itself
  and dropped back to 3.3V straight after.
- Faster to run out of guesses on a truly unwritable cart, too: the probe now
  reports which method and voltage a chip answered on, so a real result and a
  real dead end are easy to tell apart.

## v1.0.6

- Every ROM dump gets a receipt. A plain-text report lands next to the dump
  with its checksums, header checks, save type and a clear verdict, so years
  from now you can prove the file still matches the cart it came from. A bare
  .sha1 file is written too, in the standard sha1sum format, for checking with
  ordinary command-line tools. Turn the receipts off in Settings if you want
  bare dump folders.
- Save restores get a receipt of their own. Restoring a save already read the
  data back off the cart to confirm the write landed; that result now goes on
  record in a .restore.txt next to the save file, with the file's hashes and a
  plain verdict. A restore that fails the read-back still writes its receipt,
  marked FAILED, so there's a paper trail either way.
- Tools > Re-verify a ROM or save against its receipt recomputes a file's
  hashes and compares them to what the receipt recorded. Bit rot, truncation
  and stray edits all get caught.
- Batch dumps write a receipt per cart.
- Settings only saves values you changed. Before this, the file pinned every
  default at first run, so a changed default could never reach an existing
  install.

## v1.0.5

- The app version now comes straight from this changelog's top entry, so it can't
  fall behind the release tag and trigger a false "update available" again.
- The update check now also runs on a timer while the app is open, so a release
  published mid-session gets noticed without restarting.

## v1.0.3

- Apply IPS, BPS and UPS patches to a ROM. BPS and UPS check the base ROM's
  checksum first, so a wrong or trimmed ROM gets caught instead of turning into a
  broken game.
- Bake Game Boy Game Genie codes into a ROM. Each code is checked against the
  byte already in the ROM and skipped if it doesn't match, so a code meant for a
  different version won't corrupt anything. GameShark codes are decoded for
  reference, since those write to RAM at runtime and can't live in a ROM file.
- Batch dump. Read a whole stack of carts in one go: it dumps each cart's ROM and
  save, verifies the dump, names the file, and asks you to swap in the next one.
- Library view. Point it at a folder of dumps to see what's verified good and
  which files are duplicates of each other by hash.
- Settings window. Default save folder, auto-verify, library folder, and update
  preferences now live in one place.
- The What's New window shows the actual list of changes now, pulled from this
  changelog, instead of a link out to GitHub. You can open it any time from Help.

## v1.0.1

- Fixed the version number so the app stops reporting a phantom update.
- Builds now take their version straight from the release tag, so this can't
  drift again.

## v1.0.0

First release.

- Dump Game Boy, Game Boy Color and Game Boy Advance ROMs.
- Back up and restore saves for every save type: SRAM, EEPROM (4Kbit and
  64Kbit), and Flash (512Kbit and 1Mbit), plus Game Boy battery RAM.
- Restore a save back to a cart and read it back to confirm it wrote correctly.
  This makes a battery swap safe: back up, change the battery, restore.
- Verify a dump after pulling it. Checks the Nintendo logo, header checksum,
  and the game's known-good hash, so a bad read gets caught.
- Auto-name dumps from the real game title instead of "cartridge.gba".
- Identify the flash chip on a flash cart without writing to it.
- Tell you which flash cart a game needs, since carts are locked to one save
  type.
- Patch a GBA ROM for batteryless saving, fully offline.
- Apply IPS, BPS and UPS ROM hack patches, with base-ROM checksum verification
  on BPS and UPS.
- Bake Game Boy Game Genie codes into a ROM, with an old-value safety check.
- Batch dump a stack of carts in one sitting.
- Library view showing what's verified and what's duplicated.
- Built-in updater with a What's New window.
