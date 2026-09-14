"""Execution support for the hammer deck: awakened Battle Ram / Angry
Barbarians evolutions and the elite Wizard hero form."""
import unittest

from test_pipeline import opening

from bridge.evolution_state import (ANGRY_BARBARIANS, BATTLE_RAM, BINDINGS, CardEvolutionTracker,
    is_evolved_entity)
from bridge.probe_client import ProbeClient
from agent.feature_adapter import FeatureAdapter

HAMMER_DECK = (26000020, 26000036, 26000043, 26000083, 26000017, 26000052, 28000015, 28000026)
WIZARD_HERO_ENTITY = 203000017


class BindingTests(unittest.TestCase):
    def test_bindings_cover_the_hammer_cards(self):
        self.assertIn(BATTLE_RAM, BINDINGS)
        self.assertIn(ANGRY_BARBARIANS, BINDINGS)
        self.assertIsNone(BINDINGS[BATTLE_RAM].form_card_id)  # adopted from native variants
        self.assertEqual(BINDINGS[ANGRY_BARBARIANS].cycles, 1)

    def test_angry_barbarian_unit_variants_both_match(self):
        first = {'card_id': 13000043, 'native_data_global_id': 1043953944, 'native_data_name': 'AngryBarbarian_EV1'}
        second = {'card_id': 13000043, 'native_data_global_id': 2548884181, 'native_data_name': 'AngryBarbarian_EV1_2'}
        wrong_id = {'card_id': 13000043, 'native_data_global_id': 999, 'native_data_name': 'AngryBarbarian_EV1'}
        base = {'card_id': 26000043, 'native_data_global_id': 1, 'native_data_name': 'AngryBarbarians'}
        self.assertTrue(is_evolved_entity(first, ANGRY_BARBARIANS))
        self.assertTrue(is_evolved_entity(second, ANGRY_BARBARIANS))
        self.assertFalse(is_evolved_entity(wrong_id, ANGRY_BARBARIANS))
        self.assertFalse(is_evolved_entity(base, ANGRY_BARBARIANS))

    def test_battle_ram_matches_by_name_when_asset_id_unknown(self):
        evolved = {'card_id': 13000036, 'native_data_global_id': 12345, 'native_data_name': 'BattleRam_EV1'}
        normal = {'card_id': 26000036, 'native_data_global_id': 1, 'native_data_name': 'BattleRam'}
        self.assertTrue(is_evolved_entity(evolved, BATTLE_RAM))
        self.assertFalse(is_evolved_entity(normal, BATTLE_RAM))


class TrackerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        adapter = FeatureAdapter(hero_musketeer=None, skeleton_evolution=True)
        cls.bundle = adapter.bundle

    def observe_row(self, card_id, row):
        tracker = CardEvolutionTracker(card_id)
        return tracker.observe({card_id: row}, self.bundle, 100)

    def test_battle_ram_adopts_native_variant_id_and_reaches_ready(self):
        row = {'deck_slot': 1, 'variants_known': True, 'active_form': 0, 'selected_cost': 4,
            'evolution_progress': 0, 'variants': [{'data_id': 13000036, 'form_code': 1, 'cycle_required': 2}]}
        states, issues = self.observe_row(BATTLE_RAM, row)
        self.assertEqual((states, issues), ((), []))
        row.update(evolution_progress=1, active_form=0)
        states, issues = self.observe_row(BATTLE_RAM, row)
        self.assertEqual(issues, [])
        self.assertEqual(states[0].phase.name, 'CYCLING')
        row.update(evolution_progress=2, active_form=1)
        states, issues = self.observe_row(BATTLE_RAM, row)
        self.assertEqual(issues, [])
        self.assertTrue(states[0].ready)

    def test_angry_barbarians_are_ready_after_one_cycle(self):
        row = {'deck_slot': 2, 'variants_known': True, 'active_form': 1, 'selected_cost': 6,
            'evolution_progress': 1, 'variants': [{'data_id': 13000043, 'form_code': 1, 'cycle_required': 1}]}
        states, issues = self.observe_row(ANGRY_BARBARIANS, row)
        self.assertEqual(issues, [])
        self.assertTrue(states[0].ready)
        self.assertEqual(states[0].cycle_remaining, 0)

    def test_battle_ram_catalog_mismatch_blocks(self):
        row = {'deck_slot': 1, 'variants_known': True, 'active_form': 0, 'selected_cost': 4,
            'evolution_progress': 0, 'variants': [{'data_id': 13000036, 'form_code': 1, 'cycle_required': 3}]}
        states, issues = self.observe_row(BATTLE_RAM, row)
        self.assertEqual((states, issues), ((), ['%d_evolution_catalog_unverified' % BATTLE_RAM]))


class WizardHeroGateTests(unittest.TestCase):
    def setUp(self):
        self.raw = opening()
        player = self.raw['players'][0]
        player['deck'] = list(HAMMER_DECK)
        player['card_runtime'] = []
        forms = {26000036: 0, 26000043: 0, 26000017: 2}
        bundle = FeatureAdapter(hero_musketeer=None, skeleton_evolution=True).bundle
        for i, cid in enumerate(HAMMER_DECK):
            spec_cost = int(bundle.card_specs[cid].elixir_cost)
            variants = []
            if cid == 26000036:
                variants = [{'data_id': 13000036, 'form_code': 1, 'cycle_required': 2}]
            if cid == 26000017:
                variants = [{'data_id': WIZARD_HERO_ENTITY, 'form_code': 2, 'cycle_required': None}]
            player['card_runtime'].append(dict(deck_slot=i, card_id=cid, variants_known=True,
                active_form=forms.get(cid, 0), selected_cost=spec_cost,
                evolution_progress=0, variants=variants))
        player['hand'] = [{'slot': 0, 'card_id': 26000017}, {'slot': 1, 'card_id': 26000036},
                          {'slot': 2, 'card_id': 26000083}, {'slot': 3, 'card_id': 28000026}]
        player['cycle'] = [26000043, 26000052, 26000020, 28000015]
        self.state = ProbeClient(account_id=123).parse(self.raw)
        self.adapter = FeatureAdapter(hero_musketeer=None, skeleton_evolution=True)
        self.adapter.reset_match(self.state, 'hammer')

    def own(self):
        self.state.tick += 5
        self.adapter.observe(self.state)
        _, obs = self.adapter.tensorize(self.state)
        return obs

    def ram_row(self):
        return next(r for r in self.raw['players'][0]['card_runtime'] if r['card_id'] == 26000036)

    def test_wizard_elite_form_is_playable_and_detected(self):
        obs = self.own()
        self.assertTrue(self.adapter.wizard_hero)
        self.assertTrue(self.adapter.quality['wizard_hero_execution_enabled'])
        self.assertTrue(self.adapter.quality['ability_hud_excluded'])
        self.assertTrue(obs.action_mask.hand_slots[0])
        self.assertEqual(obs.action_mask.placement_masks['0']['form_code'], 2)

    def test_battle_ram_ready_cycle_is_playable(self):
        self.own()  # first observation arms the trackers
        row = self.ram_row()
        self.state.tick += 5
        row.update(evolution_progress=2, active_form=1)
        obs = self.own()
        slot = str(next(i for i, c in enumerate(self.state.hand_cards) if c == 26000036))
        self.assertTrue(obs.action_mask.hand_slots[int(slot)])
        self.assertEqual(obs.action_mask.placement_masks[slot]['form_code'], 1)

    def test_unsupported_awakened_card_stays_masked(self):
        # Swap the wizard (deck slot 4, currently in hand) for an awakened
        # Mini P.E.K.K.A (no bridge binding).
        self.raw['players'][0]['deck'][4] = 26000018
        row = self.raw['players'][0]['card_runtime'][4]
        row['card_id'] = 26000018
        row['variants'] = [{'data_id': 13000018, 'form_code': 1, 'cycle_required': 2}]
        row.update(evolution_progress=2, active_form=1, selected_cost=4)
        self.raw['players'][0]['hand'][0]['card_id'] = 26000018
        self.state = ProbeClient(account_id=123).parse(self.raw)
        self.adapter = FeatureAdapter(hero_musketeer=None, skeleton_evolution=True)
        self.adapter.reset_match(self.state, 'hammer-mini')
        obs = self.own()
        self.assertFalse(obs.action_mask.hand_slots[0])


if __name__ == '__main__':
    unittest.main()
