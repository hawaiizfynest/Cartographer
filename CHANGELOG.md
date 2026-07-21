# Changelog

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
