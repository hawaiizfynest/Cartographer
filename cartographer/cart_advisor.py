"""
cart_advisor.py - work out what a specific game needs on a specific cart.

cart_compat.py answers the buying question: given a game, which cart should I
get. This answers the one you hit afterwards: I own this cart, I want this game
on it, what has to happen.

That question could not be answered properly before the save chip's id became
readable. A repro cart's game code describes whichever ROM is loaded, not the
board, so asking the ROM what save hardware the cart has gives the wrong answer
every time the cart is reused. The chip id comes from the board itself.

Nothing here writes to a cart or patches anything. It reports what is needed and
how well established each route is, which matters because some routes are solid
and some are not, and a caller that cannot tell them apart will present a guess
with the same confidence as a fact.

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .cart_compat import (SAVE_EEPROM_4K, SAVE_EEPROM_64K, SAVE_FLASH_1M,
                          SAVE_FLASH_512K, SAVE_NONE, SAVE_SRAM_256K)
from .flash_db import SAVE_FLASH_IDS

# How much trust a route has earned. Callers should show this, not bury it.
PROVEN = "proven"        # done end to end on hardware and known to work
EXPECTED = "expected"    # follows directly from how the parts are documented
UNPROVEN = "unproven"    # should work, nobody has confirmed it
BLOCKED = "blocked"      # known not to work, and why

CONFIDENCE_TEXT = {
    PROVEN: "Proven. This route has been run end to end on real hardware.",
    EXPECTED: "Expected. This follows from how the chip and the game behave, "
              "and there is nothing unusual about it.",
    UNPROVEN: "Unproven. It should work, but it has not been confirmed, so "
              "treat a failure as informative rather than surprising.",
    BLOCKED: "Blocked. This will not work as things stand.",
}

_LABELS = {
    SAVE_NONE: "no save",
    SAVE_EEPROM_4K: "EEPROM 4K (512 bytes)",
    SAVE_EEPROM_64K: "EEPROM 64K (8 KB)",
    SAVE_SRAM_256K: "SRAM 256K (32 KB)",
    SAVE_FLASH_512K: "Flash 512K (64 KB)",
    SAVE_FLASH_1M: "Flash 1M (128 KB)",
}

_EEPROM_KINDS = (SAVE_EEPROM_4K, SAVE_EEPROM_64K)
_FLASH_KINDS = (SAVE_FLASH_512K, SAVE_FLASH_1M)


@dataclass
class Step:
    """One action, in the order it has to happen.

    `where` names the part of the app or the physical act, so a caller can show
    the procedure without the reader having to work out which window each line
    belongs to.
    """
    where: str
    text: str

    def __str__(self) -> str:
        return f"[{self.where}] {self.text}"


@dataclass
class Advice:
    cart_summary: str
    game_summary: str
    confidence: str
    steps: list = field(default_factory=list)      # patch operations only
    procedure: list = field(default_factory=list)  # the whole run, in order
    notes: list = field(default_factory=list)

    @property
    def can_work(self) -> bool:
        return self.confidence != BLOCKED

    @property
    def needs_patching(self) -> bool:
        return bool(self.steps)


def _procedure(cart_kind: str, game_kind: str, patch_steps: list) -> list:
    """Build the full run: prepare the cart, patch, write, test, then check.

    The patch steps are the interesting part but they are not the part people
    get wrong. Backing up the save that is already on the cart, blanking the
    area so the game is not handed a previous game's leftovers, and setting the
    override so a backup comes off at the right size are all easy to skip and
    each one costs a whole cycle to discover.
    """
    steps = [Step("Cart", "Insert the cart and set the GBA/GBC voltage switch "
                          "before connecting.")]
    # The override has to be set before the first backup, not after it. Every
    # read and write of the save area from here on depends on it, including the
    # backup that is meant to be the safety net, and a backup taken at the wrong
    # size is not one.
    if cart_kind != game_kind:
        steps.append(Step(
            "Override", f"Set Tools > Override save type to {cart_kind}, before "
                        f"touching the save. The game code claims "
                        f"{label(game_kind)} and everything below reads and "
                        f"writes the save area at whatever size is set here."))
    steps.append(Step("Backup", "Back up whatever save is on the cart now. Keep "
                                "that file; the steps below overwrite it."))
    for step in patch_steps:
        # Guard: the override is generated above, at the point it is needed. A
        # second one arriving through the patch list would contradict it.
        if step.where != "Override":
            steps.append(step)
    steps.append(Step("Blank", "Restore a blank save file, all 0xFF and the "
                               "full size of the cart's save area, then read it "
                               "back to confirm. A game handed another game's "
                               "leftovers reports them as corrupt whether or "
                               "not the patch worked."))
    steps.append(Step("Write", "Write the patched ROM and let the verify "
                               "finish."))
    steps.append(Step("Test", "Power cycle, boot, save in game, power off, "
                              "wait, power on. The save has to survive the "
                              "power cycle, not just appear during play."))
    steps.append(Step("Check", f"Whatever happens, back the save area up on "
                               f"{cart_kind} and inspect it. All 0xFF means the "
                               f"game never wrote and the patch is the suspect. "
                               f"Real data that the game will not load means it "
                               f"wrote and could not read its own save back, "
                               f"which is a narrower problem."))
    return steps


def label(kind: str) -> str:
    return _LABELS.get(kind, kind or "unknown")


def cart_kind_from_chip(chip_id: int) -> str:
    """Map a save flash chip id to the save kind that cart can host.

    Capacity is what decides it. A game checks the save chip's id before writing
    and drives it as the part it expects, so a 128 KB chip hosts Flash 1M saves
    and a 64 KB chip hosts Flash 512K ones. Returns "" for an unknown id.
    """
    known = SAVE_FLASH_IDS.get(chip_id)
    if not known:
        return ""
    _, capacity = known
    if capacity >= 131072:
        return SAVE_FLASH_1M
    return SAVE_FLASH_512K


def advise(cart_kind: str, game_kind: str, chip_name: str = "") -> Advice:
    """Say what `game_kind` needs to save on a cart whose hardware is
    `cart_kind`. Both use the save kind strings from gbxcart.SAVE_LAYOUT."""
    cart_desc = label(cart_kind)
    if chip_name:
        cart_desc = f"{chip_name}, {cart_desc}"
    advice = Advice(cart_summary=cart_desc, game_summary=label(game_kind),
                    confidence=EXPECTED)

    if not cart_kind:
        advice.confidence = UNPROVEN
        advice.procedure = [Step("Identify", "Run Tools > Identify save chip "
                                             "and come back.")]
        advice.notes.append(
            "The cart's save hardware is unknown. Run Tools > Identify save "
            "chip first; without it the only thing describing the save is the "
            "game code, which on a repro cart describes the ROM rather than "
            "the board.")
        return advice

    if game_kind == SAVE_NONE:
        advice.confidence = EXPECTED
        advice.procedure = [
            Step("Write", "Write the ROM and let the verify finish."),
            Step("Test", "Power cycle and boot. Nothing else to do."),
        ]
        advice.notes.append(
            "This game does not save, so it runs on any cart and the save "
            "hardware is irrelevant.")
        return advice

    # The straightforward case: the cart already is what the game asks for.
    if game_kind == cart_kind:
        advice.confidence = PROVEN if cart_kind == SAVE_FLASH_1M else EXPECTED
        advice.procedure = _procedure(cart_kind, game_kind, [])
        advice.notes.append(
            "The game asks for exactly what this cart has, so it saves with no "
            "patching at all. This is always the route to prefer.")
        return advice

    # Flash game, flash cart, wrong size.
    if game_kind in _FLASH_KINDS and cart_kind in _FLASH_KINDS:
        if game_kind == SAVE_FLASH_1M and cart_kind == SAVE_FLASH_512K:
            advice.confidence = BLOCKED
            advice.notes.append(
                "The game needs 128 KB of save and this cart holds 64 KB. No "
                "patch creates storage that is not there.")
            return advice
        advice.confidence = UNPROVEN
        advice.notes.append(
            "The game expects a 512K part and this cart holds a 1M one. Both "
            "drive flash the same way, so try it with no patch at all first. "
            "The only thing that can stop it is the game reading the chip id "
            "and not recognising a part from the other size class.")
        advice.notes.append(
            "There is no patch route here if it refuses. The flash patcher "
            "hooks Nintendo's SRAM routines or an EEPROM game that has been "
            "SRAM-patched, and a flash game has neither. Running the SRAM patch "
            "first does not help: it rewrites the flash routines in place "
            "rather than turning them into SRAM ones, so the flash patcher "
            "then finds nothing to hook.")
        advice.procedure = _procedure(cart_kind, game_kind, [])
        return advice

    # SRAM game onto a flash cart. One patch, and confirmed working.
    if cart_kind in _FLASH_KINDS and game_kind == SAVE_SRAM_256K:
        # Run end to end on a Flash 1M cart. A 512K cart follows from the same
        # code: the payload's SRAM path hardcodes its spread and addresses the
        # same 64 KB window whichever size the chip is, so nothing about it
        # depends on the capacity. Confirmed is still not the same as tested.
        advice.confidence = PROVEN if cart_kind == SAVE_FLASH_1M else EXPECTED
        advice.steps.append(Step(
            "Patcher", "Flash 512K patch, on the stock ROM. No SRAM patch "
                       "first; the game already writes the way the payload "
                       "expects. Leave the load factor on 'let the game "
                       "decide'."))
        advice.procedure = _procedure(cart_kind, game_kind, advice.steps)
        advice.notes.append(
            "The patcher's log should name WriteSram, ReadSram and VerifySram. "
            "EEPROM hooks there would mean the ROM is not what it claims to "
            "be.")
        advice.notes.append(
            "A save written this way lands one byte every two across the full "
            "64 KB, so a dump of the save area reads as spacing 2. Anything "
            "else is worth looking at even if the game saves.")
        return advice

    # EEPROM game onto a flash cart. The long way round.
    if cart_kind in _FLASH_KINDS and game_kind in _EEPROM_KINDS:
        advice.confidence = UNPROVEN
        if game_kind in _EEPROM_KINDS:
            advice.steps.append(Step(
                "Patcher", "SRAM patch only, on the stock ROM. This moves the "
                           "game off EEPROM and onto SRAM style access, and "
                           "stops there."))
        # An SRAM game goes straight in; only an EEPROM game has an earlier
        # output to feed forward, and saying otherwise sends people looking for
        # a file that was never made.
        source = ("the file the previous step wrote"
                  if game_kind in _EEPROM_KINDS else "the stock ROM")
        advice.steps.append(Step(
            "Patcher", f"Flash 512K patch, on {source}. Leave the load factor "
                       f"on 'let the game decide' unless you have a reason "
                       f"not to. This redirects the save writes onto the flash "
                       f"chip through an on-cart payload."))
        advice.procedure = _procedure(cart_kind, game_kind, advice.steps)
        advice.notes.append(
            "This route has more moving parts than any other. The payload "
            "picks how widely it spreads a save from the EEPROM geometry the "
            "game reports at boot, and on a cart with no EEPROM nothing "
            "physical answers that, so the figure comes from whatever the SRAM "
            "patch left behind.")
        advice.notes.append(
            "Known: the same payload drives these chips correctly for an SRAM "
            "game, which shares its erase and program routines and asks more "
            "of the stack, not less. So a failure here is in the EEPROM "
            "geometry rather than in the flash writing, and the metadata "
            "pointer the payload reads at run time is the place to look.")
        return advice

    # Flash game onto an SRAM or EEPROM cart.
    if game_kind in _FLASH_KINDS and cart_kind in (SAVE_SRAM_256K,) + _EEPROM_KINDS:
        advice.confidence = BLOCKED
        advice.notes.append(
            f"A {label(game_kind)} game drives a flash chip with commands this "
            f"cart has nothing to interpret them with. A batteryless SRAM cart "
            f"is the usual answer for these, using Prepare ROM rather than "
            f"this route.")
        return advice

    # EEPROM game onto an SRAM cart, the classic patch.
    if cart_kind == SAVE_SRAM_256K and game_kind in _EEPROM_KINDS:
        advice.confidence = EXPECTED
        advice.steps.append(Step(
            "Patcher", "SRAM patch only. That is the whole job; no flash patch "
                       "is needed, because the cart already provides the "
                       "storage the patched game expects."))
        advice.procedure = _procedure(cart_kind, game_kind, advice.steps)
        return advice

    # SRAM game onto an EEPROM cart, or EEPROM sizes that do not line up.
    if cart_kind in _EEPROM_KINDS:
        advice.confidence = BLOCKED
        advice.notes.append(
            f"This cart provides {label(cart_kind)} and the game wants "
            f"{label(game_kind)}. EEPROM carts hold only what they are, and "
            f"there is no patch route onto them.")
        return advice

    advice.confidence = UNPROVEN
    advice.notes.append(
        f"No established route from {label(game_kind)} to {label(cart_kind)}. "
        f"Treat anything here as an experiment.")
    return advice
