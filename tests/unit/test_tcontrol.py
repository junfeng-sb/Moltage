from dataclasses import replace
import unittest

from moltage.aims.transport_evidence import (
    TransportCompletionEvidence,
    TransportSpinMode,
)
from moltage.aitranss.nlayers import NlayersValueSource
from moltage.aitranss.tcontrol import (
    OnOff,
    TControlError,
    TControlSettings,
    TControlProposal,
    parse_tcontrol,
    render_tcontrol,
    render_tcontrol_replacing_self_energy,
    render_tcontrol_with_self_energy,
)
from moltage.app.electrode_provenance import provenance_from_applied_electrodes
from moltage.junction.electrode_builder import (
    apply_electrode_placement,
    propose_electrode_placement,
)
from moltage.junction.electrode_lattice_extension import (
    add_lattice_extension,
    enumerate_lattice_extension_candidates,
)
from moltage.junction.electrode_surface import propose_electrode_surfaces
from synthetic_structure_test_support import synthetic_step2_state
from test_electrode_surface import accepted_shape


class TControlTests(unittest.TestCase):
    def setUp(self):
        self.structure, provenance = accepted_shape()
        self.surface = propose_electrode_surfaces(self.structure, provenance)
        self.settings = TControlSettings(
            natoms=128,
            nsaos=512,
            lsurc=73,
            lsurx=58,
            lsury=53,
            rsurc=128,
            rsurx=113,
            rsury=108,
        )

    def test_exact_default_nonspin_render(self):
        text = render_tcontrol(
            self.settings,
            self.structure,
            TransportSpinMode.NONE,
        )
        self.assertEqual(
            text,
            '#input data for the "aitranss" module\n'
            "$aims_input on\n"
            "$landauer on\n"
            "$coord   file=geometry.in\n"
            "$natoms  128\n"
            "$basis   file=basis-indices.out\n"
            "$read_omat file=omat.aims\n"
            "$scfmo   file=mos.aims\n"
            "$nsaos   512\n"
            "$lsurc   73\n"
            "$lsurx   58\n"
            "$lsury   53\n"
            "$rsurc   128\n"
            "$rsurx   113\n"
            "$rsury   108\n"
            "$nlayers 4\n"
            "$s1i     0.1d0\n"
            "$s2i     0.05d0\n"
            "$s3i     0.025d0\n"
            "$ener   -0.3200\n"
            "$estep   0.0002\n"
            "$eend    -0.07\n"
            "$output  file=TE.dat\n"
            "$testing off\n"
            "$ecp on\n"
            "$end\n",
        )
        self.assertNotIn("\r", text)

    def test_user_overrides_and_spin_wiring(self):
        changed = replace(
            self.settings,
            lsurc=72,
            nlayers=5,
            s1i="0.2d0",
            s2i="0.1d0",
            s3i="0.05d0",
            ener="-0.4",
            estep="0.001",
            eend="-0.05",
            output_filename="custom.dat",
            testing=OnOff.ON,
            ecp=OnOff.OFF,
        )
        text = render_tcontrol(changed, self.structure, TransportSpinMode.COLLINEAR)
        self.assertIn("$uhfmo_alpha file=alpha.aims", text)
        self.assertIn("$uhfmo_beta  file=beta.aims", text)
        self.assertNotIn("$scfmo", text)
        for expected in ("$lsurc   72", "$nlayers 5", "$s1i     0.2d0", "$output  file=custom.dat", "$testing on", "$ecp off"):
            self.assertIn(expected, text)
        self.assertEqual(self.settings.lsurc, 73)

    def test_invalid_values_are_rejected_before_render(self):
        invalid = (
            replace(self.settings, lsurx=73),
            replace(self.settings, lsurc=1),
            replace(self.settings, lsurc=1000),
            replace(self.settings, lsurc=19, lsurx=29, lsury=55),
            replace(self.settings, estep="0"),
            replace(self.settings, ener="-0.01", eend="-0.07"),
        )
        for settings in invalid:
            with self.subTest(settings=settings):
                with self.assertRaises(TControlError):
                    render_tcontrol(settings, self.structure, TransportSpinMode.NONE)
        for keyword in (
            {"natoms": 0},
            {"nsaos": 0},
            {"nlayers": 0},
            {"s1i": "NaN"},
            {"s2i": "Infinity"},
            {"output_filename": "../TE.dat"},
        ):
            with self.subTest(keyword=keyword):
                with self.assertRaises((TControlError, ValueError)):
                    replace(self.settings, **keyword)

    def test_nlayers_initial_values_are_table_driven_and_source_typed(self):
        for layers, expected in (
            (4, (2, NlayersValueSource.AIMS_RECOMMENDED)),
            (5, (3, NlayersValueSource.AIMS_RECOMMENDED)),
            (6, (4, NlayersValueSource.USER_SPECIFIED)),
        ):
            structure, provenance = accepted_shape(layers)
            surface = propose_electrode_surfaces(structure, provenance)
            proposal = TControlProposal.from_evidence(
                TransportCompletionEvidence(
                    len(structure), 512, TransportSpinMode.NONE, "a" * 64
                ),
                surface,
            )
            self.assertEqual((proposal.nlayers, proposal.nlayers_source), expected)
        for layers in (2, 3, 7, 8, 9, 10):
            structure, provenance = accepted_shape(layers)
            surface = propose_electrode_surfaces(structure, provenance)
            proposal = TControlProposal.from_evidence(
                TransportCompletionEvidence(
                    len(structure), 512, TransportSpinMode.NONE, "a" * 64
                ),
                surface,
            )
            self.assertIsNone(proposal.nlayers)
            self.assertIsNone(proposal.nlayers_source)
            with self.assertRaisesRegex(TControlError, "not configured"):
                TControlSettings.from_proposal(proposal)

    def test_lattice_extensions_do_not_change_nlayers_value_or_source(self):
        structure, connectivity, _anchors, sites = synthetic_step2_state()
        for layers in (6, 7):
            with self.subTest(layers=layers):
                applied = apply_electrode_placement(
                    structure,
                    connectivity,
                    propose_electrode_placement(
                        structure,
                        connectivity,
                        sites,
                        sites,
                        pyramid_layers=layers,
                    ),
                )
                before_surface = propose_electrode_surfaces(
                    applied.structure,
                    provenance_from_applied_electrodes(applied),
                )
                candidate = next(
                    item
                    for item in enumerate_lattice_extension_candidates(applied)
                    if item.side == "LEFT" and item.layer_index == 0
                )
                extended = add_lattice_extension(applied, candidate).applied
                after_surface = propose_electrode_surfaces(
                    extended.structure,
                    provenance_from_applied_electrodes(extended),
                )
                before = TControlProposal.from_evidence(
                    TransportCompletionEvidence(
                        len(applied.structure),
                        512,
                        TransportSpinMode.NONE,
                        "a" * 64,
                    ),
                    before_surface,
                )
                after = TControlProposal.from_evidence(
                    TransportCompletionEvidence(
                        len(extended.structure),
                        512,
                        TransportSpinMode.NONE,
                        "b" * 64,
                    ),
                    after_surface,
                )

                self.assertEqual(after_surface.pyramid_layers, layers)
                self.assertEqual(after.nlayers, before.nlayers)
                self.assertEqual(after.nlayers_source, before.nlayers_source)
                self.assertEqual(after.nlayers_evidence, before.nlayers_evidence)

    def test_attempt01_parse_and_exact_self_energy_insertion(self):
        attempt01 = render_tcontrol(
            self.settings,
            self.structure,
            TransportSpinMode.NONE,
        ).encode()
        parsed = parse_tcontrol(attempt01, self.structure)
        self.assertEqual(parsed.settings, self.settings)
        self.assertIs(parsed.spin_mode, TransportSpinMode.NONE)
        self.assertIsNone(parsed.self_energy_filename)

        retry = render_tcontrol_with_self_energy(
            attempt01,
            "self.energy.retry02.in",
            self.structure,
        )
        self.assertEqual(
            retry,
            attempt01.replace(
                b"$end\n",
                b"$self_energy file=self.energy.retry02.in\n$end\n",
            ),
        )
        reparsed = parse_tcontrol(retry, self.structure)
        self.assertEqual(reparsed.settings, self.settings)
        self.assertEqual(reparsed.self_energy_filename, "self.energy.retry02.in")

    def test_parser_rejects_unknown_tags_and_duplicate_self_energy(self):
        attempt01 = render_tcontrol(
            self.settings,
            self.structure,
            TransportSpinMode.NONE,
        )
        with self.assertRaises(TControlError):
            parse_tcontrol(attempt01.replace("$end", "$mystery on\n$end"), self.structure)
        retry = render_tcontrol_with_self_energy(
            attempt01,
            "self.energy.retry02.in",
            self.structure,
        )
        with self.assertRaises(TControlError):
            render_tcontrol_with_self_energy(
                retry,
                "self.energy.retry02.in",
                self.structure,
            )

    def test_retry03_replaces_only_attempt02_self_energy_filename(self):
        attempt01 = render_tcontrol(
            self.settings,
            self.structure,
            TransportSpinMode.NONE,
        ).encode()
        attempt02 = render_tcontrol_with_self_energy(
            attempt01,
            "self.energy.retry02.in",
            self.structure,
        )

        retry03 = render_tcontrol_replacing_self_energy(
            attempt02,
            "self.energy.retry02.in",
            "self.energy.retry03.in",
            self.structure,
        )

        self.assertEqual(
            retry03,
            attempt02.replace(
                b"$self_energy file=self.energy.retry02.in\n",
                b"$self_energy file=self.energy.retry03.in\n",
            ),
        )
        parsed = parse_tcontrol(retry03, self.structure)
        self.assertEqual(parsed.settings, self.settings)
        self.assertIs(parsed.spin_mode, TransportSpinMode.NONE)
        self.assertEqual(parsed.self_energy_filename, "self.energy.retry03.in")
        with self.assertRaisesRegex(TControlError, "expected self-energy"):
            render_tcontrol_replacing_self_energy(
                attempt02,
                "self.energy.retry01.in",
                "self.energy.retry03.in",
                self.structure,
            )


if __name__ == "__main__":
    unittest.main()
