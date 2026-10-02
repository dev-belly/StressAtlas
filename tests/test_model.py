from dataclasses import replace
from decimal import Inexact, ROUND_DOWN, localcontext
import math
import unittest

import numpy as np

from stressatlas.core import Loan, Scenario, SectorShock, make_drivers, paired_delta, sector_attribution, simulate, stressed_terms, tail_metrics, tail_weights, validate_portfolio
from stressatlas.bundle import decode
from stressatlas.demo import demo_inputs


def loan(**changes):
    fields=dict(loan_id="L1",obligor_id="B1",sector="retail",ead=1000.0,pd=0.2,lgd=0.5,rho=0.25)
    fields.update(changes)
    return Loan(**fields)


class ModelTests(unittest.TestCase):
    def test_zero_and_unit_pd_are_exact(self):
        loans=[loan(pd=0),loan(loan_id="L2",obligor_id="B2",pd=1)]
        result=simulate(loans,Scenario("base"),make_drivers(loans,500,1))
        np.testing.assert_array_equal(result.losses,np.full(500,500.0))
        self.assertEqual(result.default_rates,{"B1":0.0,"B2":1.0})

    def test_zero_lgd_has_zero_loss(self):
        loans=[loan(pd=1,lgd=0)]
        result=simulate(loans,Scenario("base"),make_drivers(loans,200,1))
        self.assertEqual(result.analytic_el,0)
        self.assertTrue(np.all(result.losses==0))

    def test_same_borrower_loans_share_one_default_event(self):
        loans=[loan(),loan(loan_id="L2",ead=2000,lgd=0.75)]
        result=simulate(loans,Scenario("base"),make_drivers(loans,1000,1))
        self.assertEqual(set(result.losses),{0,2000})

    def test_splitting_position_preserves_loss_distribution(self):
        whole=[loan(ead=4000)]
        split=[loan(ead=1000),loan(loan_id="L2",ead=3000)]
        a=simulate(whole,Scenario("base"),make_drivers(whole,1000,7))
        b=simulate(split,Scenario("base"),make_drivers(split,1000,7))
        np.testing.assert_array_equal(a.losses,b.losses)
        self.assertEqual(a.driver_fingerprint,b.driver_fingerprint)

    def test_portfolio_input_order_is_irrelevant(self):
        loans,scenarios,config=decode(demo_inputs(1000,11))
        a=simulate(loans,scenarios[0],make_drivers(loans,1000,11))
        b=simulate(list(reversed(loans)),scenarios[0],make_drivers(list(reversed(loans)),1000,11))
        np.testing.assert_array_equal(a.losses,b.losses)

    def test_chunk_size_does_not_change_paths(self):
        loans,scenarios,_=decode(demo_inputs(1501,2))
        drivers=make_drivers(loans,1501,2)
        a=simulate(loans,scenarios[1],drivers,chunk_size=7)
        b=simulate(loans,scenarios[1],drivers,chunk_size=4096)
        np.testing.assert_array_equal(a.losses,b.losses)
        np.testing.assert_array_equal(a.sector_losses,b.sector_losses)

    def test_probability_frequency_matches_marginal_pd(self):
        loans=[loan(pd=0.2,rho=0.8)]
        result=simulate(loans,Scenario("base"),make_drivers(loans,80000,123))
        tolerance=6*math.sqrt(0.2*0.8/80000)
        self.assertLess(abs(result.default_rates["B1"]-0.2),tolerance)

    def test_analytic_el_is_independent_of_asset_correlation(self):
        loans,_,_=decode(demo_inputs(3000,1))
        bank=make_drivers(loans,3000,1)
        independent=simulate(loans,Scenario("independent",rho_multiplier=0),bank)
        correlated=simulate(loans,Scenario("correlated",rho_multiplier=3),bank)
        self.assertEqual(independent.analytic_el,correlated.analytic_el)

    def test_simulated_mean_is_near_analytic_el(self):
        loans,scenarios,_=decode(demo_inputs(20000,20261001))
        result=simulate(loans,scenarios[0],make_drivers(loans,20000,20261001))
        metrics=tail_metrics(result.losses)
        self.assertLess(abs(metrics["mean_loss"]-result.analytic_el),6*metrics["mean_mc_se"])

    def test_monotone_pd_lgd_ead_stress_is_pathwise_monotone(self):
        loans,scenarios,_=decode(demo_inputs(4000,1))
        drivers=make_drivers(loans,4000,1)
        base=simulate(loans,scenarios[0],drivers)
        mild=simulate(loans,scenarios[1],drivers)
        severe=simulate(loans,scenarios[2],drivers)
        self.assertTrue(np.all(mild.losses>=base.losses))
        self.assertTrue(np.all(severe.losses>=mild.losses))

    def test_seed_repeatability_and_change(self):
        loans=[loan()]
        a=simulate(loans,Scenario("base"),make_drivers(loans,1000,1))
        b=simulate(loans,Scenario("base"),make_drivers(loans,1000,1))
        c=simulate(loans,Scenario("base"),make_drivers(loans,1000,2))
        np.testing.assert_array_equal(a.losses,b.losses)
        self.assertFalse(np.array_equal(a.losses,c.losses))

    def test_common_random_numbers_allow_zero_baseline_delta(self):
        loans=[loan()]
        base=simulate(loans,Scenario("base"),make_drivers(loans,1000,1))
        self.assertEqual(paired_delta(base,base),{"mean_delta":0.0,"paired_mc_se":0.0})

    def test_paired_comparison_rejects_different_driver_banks(self):
        loans=[loan()]
        a=simulate(loans,Scenario("base"),make_drivers(loans,1000,1))
        b=simulate(loans,Scenario("base"),make_drivers(loans,1000,2))
        with self.assertRaises(ValueError):
            paired_delta(a,b)

    def test_paired_se_is_calculated_from_path_differences(self):
        loans=[loan()]; drivers=make_drivers(loans,1000,1)
        a=simulate(loans,Scenario("base"),drivers)
        b=simulate(loans,Scenario("stress",pd_odds_multiplier=2),drivers)
        delta=paired_delta(b,a)
        self.assertAlmostEqual(delta["paired_mc_se"],float(np.std(b.losses-a.losses,ddof=1)/math.sqrt(1000)))

    def test_sector_es_contributions_add_to_portfolio_es(self):
        loans,scenarios,_=decode(demo_inputs(2000,1))
        result=simulate(loans,scenarios[2],make_drivers(loans,2000,1))
        self.assertAlmostEqual(sum(sector_attribution(result).values()),tail_metrics(result.losses)["es"],places=6)

    def test_scenario_shocks_apply_only_to_named_sector(self):
        scenario=Scenario("target",sector_shocks=(SectorShock("retail",pd_odds_multiplier=2),))
        retail=stressed_terms(loan(),scenario)
        tech=stressed_terms(loan(sector="tech"),scenario)
        self.assertGreater(retail[0],tech[0])
        self.assertEqual(tech[0],0.2)

    def test_lgd_and_rho_are_clipped_at_valid_boundaries(self):
        terms=stressed_terms(loan(lgd=.9,rho=.9),Scenario("clip",lgd_add=.5,rho_multiplier=3))
        self.assertEqual(terms[1],1)
        self.assertEqual(terms[3],1)

    def test_rho_one_is_supported(self):
        loans=[loan(rho=1)]
        result=simulate(loans,Scenario("base"),make_drivers(loans,1000,1),global_share=1)
        self.assertTrue(np.all(np.isfinite(result.losses)))


class TailTests(unittest.TestCase):
    def test_tail_ranks_ignore_callers_decimal_context(self):
        losses = list(range(101))
        expected = tail_metrics(losses, .985)
        self.assertEqual(expected["var"], 99)
        self.assertEqual(expected["tail_mass"], 1.515)
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_DOWN
            context.traps[Inexact] = True
            self.assertEqual(tail_metrics(losses, .985), expected)

    def test_extreme_valid_tail_levels_keep_valid_empirical_ranks(self):
        for alpha, expected_var in ((5e-324, 0), (math.nextafter(1.0, 0.0), 100)):
            with self.subTest(alpha=alpha), localcontext() as context:
                context.prec = 1
                context.Emax = 1
                context.Emin = -1
                result = tail_metrics(list(range(101)), alpha)
                self.assertEqual(result["var"], expected_var)
                self.assertGreater(result["tail_mass"], 0)
                self.assertGreaterEqual(result["es"], result["var"])

    def test_fractional_tail_mass_has_hand_calculated_es(self):
        result=tail_metrics([0,1,2,3,4],.5)
        self.assertEqual(result["var"],2)
        self.assertAlmostEqual(result["es"],3.2)
        self.assertEqual(result["tail_mass"],2.5)

    def test_discrete_var_ties_do_not_include_the_whole_distribution(self):
        result=tail_metrics([0,0,10,10],.5)
        self.assertEqual(result["var"],0)
        self.assertEqual(result["es"],10)
        naive=np.mean(np.array([0,0,10,10])[np.array([0,0,10,10])>=result["var"]])
        self.assertNotEqual(naive,result["es"])

    def test_tied_boundary_attribution_is_order_invariant(self):
        losses=np.array([10,10,0,0])
        sectors=np.array([[10,0],[0,10],[0,0],[0,0]])
        _,weights,_=tail_weights(losses,.75)
        np.testing.assert_allclose(np.sum(weights[:,None]*sectors,axis=0),[5,5])
        order=[1,0,2,3]
        _,other_weights,_=tail_weights(losses[order],.75)
        np.testing.assert_allclose(np.sum(other_weights[:,None]*sectors[order],axis=0),[5,5])

    def test_tail_weights_are_nonnegative_and_sum_to_one(self):
        for alpha in (.01,.25,.975,.99):
            _,weights,_=tail_weights([0,0,1,1,1,4,9],alpha)
            self.assertTrue(np.all(weights>=0))
            self.assertAlmostEqual(weights.sum(),1)

    def test_es_is_at_least_var(self):
        for alpha in (.01,.5,.975,.99):
            result=tail_metrics([0,1,1,3,4,8],alpha)
            self.assertGreaterEqual(result["es"],result["var"])

    def test_all_zero_losses_have_zero_metrics(self):
        result=tail_metrics([0,0,0],.99)
        self.assertEqual(result["var"],0)
        self.assertEqual(result["es"],0)

    def test_invalid_tail_inputs_are_rejected(self):
        for losses in ([],[float("nan")],[float("inf")],[-1,2],[[1,2]], [True,False],["0","1"]):
            with self.subTest(losses=losses),self.assertRaises(ValueError):
                tail_metrics(losses)
        for alpha in (0,1,-.1,True,float("nan")):
            with self.subTest(alpha=alpha),self.assertRaises(ValueError):
                tail_metrics([1,2],alpha)


class ContractTests(unittest.TestCase):
    def test_duplicate_loan_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_portfolio([loan(),loan()])

    def test_inconsistent_borrower_terms_are_rejected(self):
        for fields in ({"pd":.3},{"rho":.8},{"sector":"tech"}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                validate_portfolio([loan(),loan(loan_id="L2",**fields)])

    def test_invalid_pd_lgd_rho_and_ead_are_rejected(self):
        for name in ("pd","lgd","rho"):
            for value in (-1,1.1,True,float("nan")):
                with self.subTest(name=name,value=value),self.assertRaises(ValueError):
                    loan(**{name:value})
        for ead in (0,-1,True,float("inf"),1e16):
            with self.subTest(ead=ead),self.assertRaises(ValueError):
                loan(ead=ead)

    def test_unknown_sector_and_duplicate_scenarios_are_rejected(self):
        data=demo_inputs(200,1)
        data["scenarios"][0]["sector_shocks"]=[{"sector":"unknown"}]
        with self.assertRaises(ValueError):
            decode(data)
        data=demo_inputs(200,1);data["scenarios"][1]["name"]="baseline"
        with self.assertRaises(ValueError):
            decode(data)

    def test_invalid_paths_seeds_and_chunk_sizes_are_rejected(self):
        for paths,seed in ((1,1),(True,1),(10,-1),(10,True),(10,2**32)):
            with self.subTest(paths=paths,seed=seed),self.assertRaises(ValueError):
                make_drivers([loan()],paths,seed)
        with self.assertRaises(ValueError):
            simulate([loan()],Scenario("base"),make_drivers([loan()],10,1),chunk_size=0)

    def test_invalid_shock_and_duplicate_sector_are_rejected(self):
        for fields in ({"pd_odds_multiplier":0},{"rho_multiplier":-1},{"lgd_add":2},{"ead_multiplier":float("inf")}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                Scenario("bad",**fields)
        shock=SectorShock("retail")
        with self.assertRaises(ValueError):
            Scenario("bad",sector_shocks=(shock,shock))

    def test_driver_universe_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            simulate([loan(obligor_id="other")],Scenario("base"),make_drivers([loan()],100,1))

    def test_random_driver_arrays_are_immutable(self):
        drivers=make_drivers([loan()],100,1)
        with self.assertRaises(ValueError):
            drivers.global_z[0]=0
