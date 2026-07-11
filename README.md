# Cartographer

A desktop app for backing up, restoring and flashing Game Boy, Game Boy Color and
Game Boy Advance cartridges. It talks to a GBxCart RW (or compatible clone, like
the "Cyclone" board I have) over USB and gives you a point-and-click way to pull
ROMs and saves off a cart, put saves back, and check that a dump came out clean.

I built this because the software that shipped with my flasher was a dead end, and
because I wanted a batteryless save patcher built right into the same tool instead
of juggling three separate command-line programs every time.

![Cartographer](assets/preview.png)

## What it does

- Dumps GB, GBC and GBA ROMs, with the ROM size detected automatically on GBA.
- Backs up and restores saves for every save type: SRAM, EEPROM (4Kbit and
  64Kbit), Flash (512Kbit and 1Mbit with bank switching), and GB/GBC battery RAM.
  Game Boy saves read the correct amount per cart, so MBC2 (512 bytes), 2 KB and
  full 8 KB carts all come out right.
- Verifies a dump after it's pulled. It checks the Nintendo logo and header
  checksum, and on Game Boy the global checksum too, then computes CRC32 and
  SHA-1 and tells you whether the dump matches a known-good release. A single bad
  byte in the middle of a 16 MB ROM gets caught by the hash even when the header
  still looks fine.
- Restores saves back to a cart and reads them straight back to confirm the write
  landed. This is what makes a battery swap safe: back up the save, change the
  dead battery, restore, done.
- Names your dumps for you. The GBA header only stores a short 12-character
  title, so Cartographer looks up the full game name from the cart's game code,
  and upgrades to the exact release name from the SHA-1 once the whole ROM is
  dumped. A dump of Dora lands as `Dora the Explorer The Search for Pirate Pig's
  Treasure (USA).gba` instead of `cartridge.gba`.
- Identifies the flash chip on a flash cart without writing anything to it, and
  tells you the chip, its size, and whether it's one the tooling knows how to
  write. Unknown chips get reported honestly with their raw ID rather than a
  guess.
- Tells you which flash cart a game needs. GBA flash carts are locked to one save
  type because the game checks the save chip's ID, so this saves you from buying
  the wrong one.
- Patches a GBA ROM for batteryless saving, entirely offline, no device needed.

## The batteryless patcher

The headline reason this project exists. A lot of cheap flash carts have SRAM but
no battery, so the save is gone the moment you power off. The batteryless patch
redirects the game's save into SRAM and flushes it back to the ROM flash on write,
so it survives without a battery.

I did not write that patch from scratch. Cartographer bundles a port of
**metroid-maniac's** `gba-auto-batteryless-patcher`, which does the real work, and
it chains through **bbsan2k's** `Flash1M_Repro_SRAM_Patcher` first for the SRAM
step. I verified the whole chain produces byte-for-byte identical output to
running both original tools by hand on my own Emerald ROM before I trusted it.
Full credit to them, see the Thanks section below.

## Getting started

You'll need Python 3.12 or newer.

```
pip install -r requirements.txt
python run.py
```

Set the voltage switch on your cart to match what you're reading before you plug
it in. GBA sits at 3.3V, GB and GBC at 5V. On the v1.1 board the switch is
physical and the software can't override it, so Cartographer warns you if it
looks like nothing's seated for the current switch position.

Then pick your COM port, hit Connect, and Read cart info. From there the buttons
do what they say.

## Building a standalone exe

The GitHub Actions workflow builds Windows, macOS and Linux binaries on every
push, so you don't have to build anything yourself. Push from GitHub Desktop and
grab the `.exe` from the Actions tab a few minutes later. Tag a commit `v1.0.1`
(or whatever the next version is) and it also cuts a Release with the binaries
attached.

If you want to build locally:

```
pip install pyinstaller
pyinstaller --noconfirm Cartographer.spec
```

The result is a single file in `dist/`.

## Checking for updates

Cartographer checks GitHub for a newer release when it starts, and you can check
any time from Help > Check for updates. When there's a new version it shows you
the full list of changes first, so you can decide whether it's worth it. From
there you can update now, be reminded later, or tick the box to skip that version
for good.

In the built app, choosing Update handles everything: it downloads the new build,
closes Cartographer, swaps the old program for the new one, and reopens. The
download gets size-checked against what GitHub reports, and Windows builds are
checked for a valid executable header before anything gets swapped. Running from
source instead? It'll point you at GitHub Desktop, since that's how you update
the code.

After an update, a What's New window lists what changed on first launch. If you'd
rather not see it, there's a checkbox on that window to turn it off, and you can
flip it back on under Help.

## What's not done yet

- Flashing a ROM to a GBA cart. The reading, saving and patching all work; the
  write path is the next big piece and it's waiting on me getting a proper
  reflashable cart to test against, since both of my current carts are retail
  mask ROM and physically can't be written.
- The game database only has a few dozen titles seeded in it right now. Every
  dump you make gets remembered by hash, so exact names build up over time, but a
  full No-Intro import is on the list.
- Atmel-type flash saves. The common flash save path is done; the handful of
  older Atmel carts would need their own write routine.

## Thanks

This wouldn't exist without a lot of other people's work:

- **metroid-maniac** for the GBA auto batteryless patcher. That's the core
  feature and it's their code, ported into Python here.
- **bbsan2k** for the Flash1M repro SRAM patcher that handles the SRAM step.
- **insideGadgets** for the GBxCart RW hardware and the serial protocol this
  whole app is built to speak.
- **Lesserkuma** and the FlashGBX project, which was my reference for how title
  and save-type resolution should work.

The original licenses for the patchers live in the `licenses/` folder.

## License

GPL-2.0. Written by LJ "HawaiizFynest" Eblacas.

The bundled patcher ports keep their original MIT licenses (see `licenses/`).
insideGadgets' GBxCart protocol is documented under CC BY-NC-SA, so this is fine
for personal and open-source use. Cartographer is an independent client and
includes none of their code.
