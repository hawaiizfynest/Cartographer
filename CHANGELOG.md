# Changelog

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
