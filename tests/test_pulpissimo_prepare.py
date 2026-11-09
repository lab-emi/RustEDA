"""PULPissimo preparation repairs; no EDA tool or source checkout is read.

Estimate <64 MiB RSS: one small in-memory fixture and one test worker. Run
under 1 GiB / 60 s.
"""
import unittest

from integration.pulpissimo_soc.prepare import fix_sv2v_apb_interface, strip_translate_off


class PrepareRepairs(unittest.TestCase):
    def test_dropped_apb_interface_connects_to_plain_ports(self):
        source = """module wrapper(
  output wire [31:0] apb_paddr_o,
  input wire [31:0] apb_prdata_i
);
  pulp_soc i_pulp_soc(
    .apb_chip_ctrl_master_paddr_o(pulpissimo_soc_wrapper.apb_chip_ctrl_master.paddr),
    .apb_chip_ctrl_master_pprot_o(pulpissimo_soc_wrapper.apb_chip_ctrl_master.pprot),
    .apb_chip_ctrl_master_prdata_i(pulpissimo_soc_wrapper.apb_chip_ctrl_master.prdata)
  );
  assign apb_paddr_o = apb_chip_ctrl_master.paddr;
  assign apb_chip_ctrl_master.pready = 1'b1;
endmodule
"""
        fixed, repairs = fix_sv2v_apb_interface(source)
        self.assertNotIn('pulpissimo_soc_wrapper.apb_chip_ctrl_master', fixed)
        self.assertNotIn('apb_chip_ctrl_master.', fixed)
        self.assertIn('.apb_chip_ctrl_master_paddr_o(apb_paddr_o)', fixed)
        self.assertIn('.apb_chip_ctrl_master_prdata_i(apb_prdata_i)', fixed)
        self.assertEqual(repairs, 5)

    def test_translate_off_blocks_are_removed(self):
        source = """module m;
  // pragma translate_off
  initial $display("simulation only");
  // pragma translate_on
  assign y = a;
endmodule
"""
        fixed, blocks = strip_translate_off(source)
        self.assertEqual(blocks, 1)
        self.assertNotIn('simulation only', fixed)
        self.assertIn('assign y = a;', fixed)


if __name__ == '__main__':
    unittest.main()
