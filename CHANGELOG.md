# Changelog

## v1.2.2

- The save type override is remembered between launches. It used to reset every
  time the app started, which quietly put backup and restore back on the type
  the game code claims. On a repro cart that is the wrong type, and the first
  sign of it was a backup that came out the wrong size.
- Identify save chip no longer calls itself read-only when it cannot be. There
  is no separate bus for the save chip on a GBA cart, so a read-ID is a
  sequence of ordinary writes into the save address space. A flash chip reads
  those as commands and its contents are untouched, which is where the
  read-only claim came from, but a cart with plain battery-backed RAM in the
  save space has nothing decoding them and they land as data on a real save.
  The check now warns before running on anything that does not read as flash,
  and reports whether the command addresses changed, which answers whether the
  save space is flash or RAM as a side effect.
- Connecting now tries both speeds before giving up. Opening a serial port
  configures it as well, so a driver that refuses one speed can still accept
  the other, and stopping at the first refusal turned a recoverable problem
  into a port that would not open at all.
- Connection errors say what actually went wrong. Windows reports a busy port,
  a port that has gone away and a driver that refuses the settings as the same
  kind of error, and the fix for each is different, so the message now reads
  the code and gives the advice that fits instead of blaming another program
  every time.

## v1.2.1

- Fixed Cartographer closing itself when a connection fails. Opening a serial
  port can be refused by Windows, when another program holds it or when the
  writer has been unplugged since the port list was drawn, and that refusal was
  reaching the connect handler as a kind of error it did not catch. An
  uncaught error closes a Qt app outright, so the window disappeared with
  nothing on screen to say why. A failed connect now says what went wrong and
  leaves the app running. The same cover extends over the identify step, which
  is where a writer pulled mid-handshake used to land.
- Added a crash log. Anything that goes wrong and is not handled somewhere more
  specific now writes to crash.log next to the settings file and shows what
  happened, rather than closing the window.
- Reading the cart info after a connect is covered the same way. That step runs
  at the end of every connect and had the same gap, so a writer that answered
  the handshake and then stopped responding still took the window with it.

## v1.2.0

- Fixed Identify save chip reporting no answer from a chip that was answering.
  The device replies to a read-ID with two bytes and nothing more, but the tool
  read that reply the way it reads a ROM dump, which takes a 64-byte block and
  then asks the device to continue. The wait for the rest of the block timed
  out and threw away the two bytes already in hand, so every cart came back as
  "the device did not return a save flash id" no matter which chip was fitted.
  It now reads the two bytes the firmware sends.
- The chip report covers more cases. If the two bytes arrive in the reverse
  order it still names the chip and says so, instead of calling a part games
  know unrecognised. If the maker byte is one of the five used for GBA save
  chips but the device byte is unfamiliar, it says a real chip is answering and
  names the maker. A one-byte reply is called out as a short read rather than
  being treated as an id.

## v1.1.9

- Added Tools > Identify save chip. A flash save lives on its own small chip,
  separate from the large ROM chip, and games read that chip's id before writing
  a save so they know which command sequence it needs. A chip whose id a game
  does not recognise is one the game will not write to, which looks like a save
  that will not stick or reads as corrupt even though a flasher can read and
  write the same chip without trouble. This reads the id and says whether it is
  one games know. Read-only.

## v1.1.8

- Added a save type override under Tools > Override save type. Backup and
  restore normally use the save type looked up from the game code, which is
  right for an unmodified game and wrong for a patched one: a save-type patch
  rewrites where a game saves but leaves the game code alone, so the lookup
  keeps reporting the type the game used before patching. Setting an override
  makes backup and restore use the type you pick instead. The cart info panel
  marks the save type as an override while one is set, and the size mismatch
  warning now points at this when a save file does not match the detected type.

## v1.1.6

- Added a save editor. Open it from Tools > Save editor, or from the Compare
  saves tab. It shows the whole save file as hex with an ASCII column, lets you
  change any byte by typing two hex digits, and writes the result to a new file
  so the original is left alone. There is a go-to-offset box and a find box that
  takes either text or hex bytes.
- Alongside the hex view, the editor shows what is in the save: which regions
  hold data and which are blank, and any readable text with its offset. Save
  files are game-specific binary, so nothing can label the bytes for you, but
  the layout and the text runs are the landmarks worth having when you are
  finding your way around one.

## v1.1.5

- Writing a ROM now reports how long it took, both in the log and in the
  completion message.
- Added a save file comparison tool. Open Tools > ROM and save tools and pick
  the Compare saves tab. Give it two save files and it reports whether they
  match, how many bytes differ and where, and what that most likely means. Give
  it one and it tells you whether the file holds real data or is blank. This
  answers the question a save file's name and size cannot: whether a cart
  actually kept what was written to it. Back the save up, power the cart down
  and back up, back it up again, and compare the two.
- The Tools menu entry for the ROM tools window now says "ROM and save tools
  (patches, cheats, compare saves)" so the save tools are findable. It used to
  mention only patches and cheats.
- The write log now names the write mode it is actually using (buffered or
  block) instead of always saying "block". Cosmetic only; the correct command
  was always being used.

## v1.1.3

- Fast ROM writing, now with buffered writes for a little more speed. Writing a
  ROM used to program one word at a time over the cable, which was reliable but
  slow (hours for a large game). The device firmware has faster commands that
  program a whole 256-byte block on the cart at once. Write ROM to flash cart now
  picks the fastest one the cart supports: it tries the buffered write first,
  then the plain block write (each including the data-line-swapped case some
  repro carts need), and uses whichever verifies. The safety design is unchanged:
  every sector is erased, written, then read back and checked against the file,
  and the write stops at the first mismatch. If no fast command verifies, the
  write falls back to the reliable word-at-a-time path automatically. In practice
  this turns a multi-hour write into minutes.
- The fast-write check is also available on its own as Tools > Test fast block
  write, which erases sector 0, writes one test block, and reports which fast
  command works, without writing a whole ROM.

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
