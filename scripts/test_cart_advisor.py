"""
test_cart_advisor.py - tests for the cart-and-game advisor.

The advisor's value is that it distinguishes a route that has been run from one
that merely ought to work, so most of these pin the confidence rather than the
wording.

Run:  python scripts/test_cart_advisor.py

Written by LJ "HawaiizFynest" Eblacas
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartographer import cart_advisor as ca  # noqa: E402
from cartographer.cart_compat import (SAVE_EEPROM_4K, SAVE_EEPROM_64K,
                                      SAVE_FLASH_1M, SAVE_FLASH_512K,
                                      SAVE_NONE, SAVE_SRAM_256K)  # noqa: E402


def test_chip_id_decides_the_cart_not_the_game_code():
    """The whole point of reading the chip: a repro cart's game code describes
    the ROM currently on it, the chip describes the board."""
    assert ca.cart_kind_from_chip(0x09C2) == SAVE_FLASH_1M    # MX29L010, 128 KB
    assert ca.cart_kind_from_chip(0x1362) == SAVE_FLASH_1M    # Sanyo, 128 KB
    assert ca.cart_kind_from_chip(0x1CC2) == SAVE_FLASH_512K  # MX29L512, 64 KB
    assert ca.cart_kind_from_chip(0x1B32) == SAVE_FLASH_512K  # Panasonic, 64 KB
    assert ca.cart_kind_from_chip(0xDEAD) == ""


def test_matching_game_and_cart_needs_nothing():
    a = ca.advise(SAVE_FLASH_1M, SAVE_FLASH_1M)
    assert a.can_work
    assert not a.needs_patching
    assert a.confidence == ca.PROVEN


def test_unknown_cart_says_read_the_chip_first():
    a = ca.advise("", SAVE_EEPROM_4K)
    assert a.confidence == ca.UNPROVEN
    assert any("Identify save chip" in n for n in a.notes)


def test_eeprom_game_on_flash_cart_needs_both_patches_in_order():
    a = ca.advise(SAVE_FLASH_1M, SAVE_EEPROM_4K)
    assert a.can_work and a.needs_patching
    assert a.confidence == ca.UNPROVEN, "this route is not confirmed working"
    joined = " ".join(str(s) for s in a.steps)
    assert joined.index("SRAM patch") < joined.index("Flash 512K patch")
    assert any("Blank" == s.where for s in a.procedure), \
        "blanking has to be in the procedure, it is the step people skip"


def test_sram_game_on_flash_cart_skips_the_sram_step():
    a = ca.advise(SAVE_FLASH_1M, SAVE_SRAM_256K)
    assert not any("SRAM patch only" in s.text for s in a.steps)
    assert any("Flash 512K patch" in s.text for s in a.steps)
    assert len(a.steps) == 1, "one patch, not two"


def test_sram_on_flash_1m_is_proven_and_eeprom_is_not():
    """Wario Land 4 through the flash patch onto a Flash 1M cart has been run
    end to end. The EEPROM route on the same cart has not, and showing them at
    the same confidence is how someone ends up assuming their hardware is at
    fault."""
    sram = ca.advise(SAVE_FLASH_1M, SAVE_SRAM_256K)
    eeprom = ca.advise(SAVE_FLASH_1M, SAVE_EEPROM_4K)
    assert sram.confidence == ca.PROVEN
    assert eeprom.confidence == ca.UNPROVEN


def test_sram_on_512k_cart_is_expected_not_claimed_as_proven():
    """The 512K case follows from the same code but has not been run."""
    a = ca.advise(SAVE_FLASH_512K, SAVE_SRAM_256K)
    assert a.confidence == ca.EXPECTED


def test_eeprom_route_records_that_the_flash_writing_itself_is_fine():
    a = ca.advise(SAVE_FLASH_1M, SAVE_EEPROM_4K)
    joined = " ".join(a.notes)
    assert "SRAM game" in joined
    assert "geometry" in joined


def test_1m_game_on_512k_cart_is_blocked_for_space():
    a = ca.advise(SAVE_FLASH_512K, SAVE_FLASH_1M)
    assert not a.can_work
    assert a.confidence == ca.BLOCKED


def test_512k_game_on_1m_cart_is_an_id_problem_not_a_space_one():
    a = ca.advise(SAVE_FLASH_1M, SAVE_FLASH_512K)
    assert a.can_work
    assert a.confidence == ca.UNPROVEN
    assert any("id" in n for n in a.notes)


def test_512k_game_is_never_sent_through_a_patch_that_cannot_hook_it():
    """The flash patcher hooks Nintendo's SRAM routines or an SRAM-patched
    EEPROM game. A flash game has neither, and SRAM-patching it first rewrites
    the flash routines in place rather than producing SRAM ones, so the flash
    patcher then finds nothing. Suggesting either wastes a cycle."""
    a = ca.advise(SAVE_FLASH_1M, SAVE_FLASH_512K)
    assert not a.steps, "there is no patch route for a flash game"
    assert not any("Patcher" == s.where for s in a.procedure)
    assert any("no patch at all" in n for n in a.notes)


def test_eeprom_game_on_sram_cart_is_one_step():
    a = ca.advise(SAVE_SRAM_256K, SAVE_EEPROM_64K)
    assert a.confidence == ca.EXPECTED
    assert len(a.steps) == 1
    assert "SRAM patch only" in a.steps[0].text


def test_flash_game_on_eeprom_cart_is_blocked():
    a = ca.advise(SAVE_EEPROM_4K, SAVE_FLASH_1M)
    assert not a.can_work


def test_no_save_game_runs_anywhere():
    for cart in (SAVE_FLASH_1M, SAVE_SRAM_256K, SAVE_EEPROM_4K, ""):
        a = ca.advise(cart, SAVE_NONE)
        if cart:
            assert a.can_work and not a.needs_patching


def test_every_confidence_has_text():
    for level in (ca.PROVEN, ca.EXPECTED, ca.UNPROVEN, ca.BLOCKED):
        assert level in ca.CONFIDENCE_TEXT


def test_blocked_routes_never_hand_back_steps():
    """A blocked route offering steps would read as a route worth trying."""
    for cart, game in ((SAVE_FLASH_512K, SAVE_FLASH_1M),
                       (SAVE_EEPROM_4K, SAVE_FLASH_1M),
                       (SAVE_EEPROM_64K, SAVE_SRAM_256K)):
        a = ca.advise(cart, game)
        if a.confidence == ca.BLOCKED:
            assert not a.steps, f"{cart}/{game} is blocked but suggests steps"


def test_procedure_orders_backup_before_anything_destructive():
    """Backing up the save already on the cart has to come before the blank and
    the write, or the first run of this destroys whatever was there."""
    a = ca.advise(SAVE_FLASH_1M, SAVE_EEPROM_4K)
    where = [s.where for s in a.procedure]
    assert where.index("Backup") < where.index("Blank")
    assert where.index("Blank") < where.index("Write")
    assert where.index("Write") < where.index("Test")


def test_procedure_ends_by_telling_you_what_to_look_at():
    a = ca.advise(SAVE_FLASH_1M, SAVE_EEPROM_4K)
    assert a.procedure[-1].where == "Check"
    assert "0xFF" in a.procedure[-1].text


def test_matching_pair_still_gets_a_full_procedure():
    """No patching does not mean no procedure. The cart still needs backing up,
    blanking and testing."""
    a = ca.advise(SAVE_FLASH_1M, SAVE_FLASH_1M)
    assert not a.steps
    assert [s.where for s in a.procedure][:2] == ["Cart", "Backup"]
    assert any(s.where == "Test" for s in a.procedure)


def test_patch_steps_appear_inside_the_procedure_in_order():
    a = ca.advise(SAVE_FLASH_1M, SAVE_EEPROM_4K)
    texts = [s.text for s in a.procedure]
    for step in a.steps:
        assert step.text in texts
    sram = next(i for i, t in enumerate(texts) if "SRAM patch only" in t)
    flash = next(i for i, t in enumerate(texts) if "Flash 512K patch" in t)
    assert sram < flash


def test_unknown_cart_procedure_is_just_go_identify_it():
    a = ca.advise("", SAVE_EEPROM_4K)
    assert len(a.procedure) == 1
    assert a.procedure[0].where == "Identify"


def test_blocked_routes_have_no_procedure_either():
    a = ca.advise(SAVE_FLASH_512K, SAVE_FLASH_1M)
    assert not a.procedure, "a blocked route must not read like something to try"


def test_flash_patch_step_names_the_right_input_file():
    """An SRAM game has no earlier patch output to feed forward. Telling someone
    to patch "the file the previous step wrote" sends them looking for a file
    that was never made."""
    sram = ca.advise(SAVE_FLASH_1M, SAVE_SRAM_256K)
    flash_step = next(s for s in sram.steps if "Flash 512K patch" in s.text)
    assert "stock ROM" in flash_step.text
    assert "previous step" not in flash_step.text

    eeprom = ca.advise(SAVE_FLASH_1M, SAVE_EEPROM_4K)
    flash_step = next(s for s in eeprom.steps if "Flash 512K patch" in s.text)
    assert "previous step" in flash_step.text


def test_override_is_set_before_the_backup_that_depends_on_it():
    """The backup is the safety net, and it reads the save area at whatever size
    the override says. Setting the override after the backup means the net was
    taken at the wrong size, which is no net at all."""
    for game in (SAVE_SRAM_256K, SAVE_EEPROM_4K, SAVE_EEPROM_64K):
        a = ca.advise(SAVE_FLASH_1M, game)
        where = [s.where for s in a.procedure]
        assert "Override" in where, f"{game} needs an override step"
        assert where.index("Override") < where.index("Backup"), \
            f"{game}: override must come before the backup"


def test_no_override_step_when_the_cart_already_matches():
    a = ca.advise(SAVE_FLASH_1M, SAVE_FLASH_1M)
    assert "Override" not in [s.where for s in a.procedure]


def test_override_appears_once_not_twice():
    a = ca.advise(SAVE_FLASH_1M, SAVE_EEPROM_4K)
    assert [s.where for s in a.procedure].count("Override") == 1


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} cart-advisor tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
