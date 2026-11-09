# Auto-generated schematic symbols
import sys
import os

# Get project path and import kicad schematic interface
PROJECT_PATH = os.environ['PROJECT_PATH']
sys.path.append(PROJECT_PATH)
from modules.kicad_sch_interface import *
set_schematic_filename(r"D:\SchGen\ldo_test\ldo_test.kicad_sch")

### Placing center symbol 1 : Regulator_Linear:AMS1117-3.3###

center_x_1, center_y_1 = 150.0, 110.0

add_schematic_symbol(symbol_lib="Regulator_Linear", symbol_name="AMS1117-3.3", pos_x=center_x_1, pos_y=center_y_1, reference="U1", value="AMS1117-3.3", rotation=0, mirror="None")

### Placing other symbols in the Schematic with respect to the center symbol 1###

add_schematic_symbol(symbol_lib="power", symbol_name="+5V", pos_x=center_x_1 + (-13), pos_y=center_y_1 + (20), reference="#PWR1", value="+5V", rotation=0, mirror="None")
add_schematic_symbol(symbol_lib="power", symbol_name="GND", pos_x=center_x_1 + (-13), pos_y=center_y_1 + (-16), reference="#PWR2", value="GND", rotation=0, mirror="None")
add_schematic_symbol(symbol_lib="Device", symbol_name="C_Polarized", pos_x=center_x_1 + (-13), pos_y=center_y_1 + (-8), reference="C1", value="10uF", rotation=0, mirror="None")
add_schematic_symbol(symbol_lib="power", symbol_name="GND", pos_x=center_x_1 + (19), pos_y=center_y_1 + (-16), reference="#PWR_GND1", value="GND", rotation=0, mirror="None")
add_schematic_symbol(symbol_lib="power", symbol_name="+5V", pos_x=center_x_1 + (8), pos_y=center_y_1 + (20), reference="#PWR3", value="+5V", rotation=0, mirror="None")
add_schematic_symbol(symbol_lib="Device", symbol_name="C_Polarized", pos_x=center_x_1 + (8), pos_y=center_y_1 + (-8), reference="C2", value="22uF", rotation=0, mirror="None")
add_schematic_symbol(symbol_lib="power", symbol_name="GND", pos_x=center_x_1 + (8), pos_y=center_y_1 + (-16), reference="#PWR4", value="GND", rotation=0, mirror="None")

### Placing all global labels in the Schematic and connect them to the neighbor pin ###


### Connecting all wires in the Schematic ###


# Connecting C2 pin 2 (Pin ID 2 -- Name None) to #PWR4 pin 1 (Pin ID 1 -- Name None)
connect_pins("C2", "2", "#PWR4", "1")

# Connecting U1 pin 2 (Pin ID 2 -- Name None) to #PWR2 pin 1 (Pin ID 1 -- Name None)
connect_pins("U1", "2", "#PWR2", "1")

# Connecting C1 pin 2 (Pin ID 2 -- Name None) to #PWR2 pin 1 (Pin ID 1 -- Name None)
connect_pins("C1", "2", "#PWR2", "1")

# Connecting #PWR3 pin +5V (Pin ID 1 -- Name +5V) to C2 pin 1 (Pin ID 1 -- Name None)
connect_pins("#PWR3", "+5V", "C2", "1")

# Connecting #PWR1 pin +5V (Pin ID 1 -- Name +5V) to C1 pin 1 (Pin ID 1 -- Name None)
connect_pins("#PWR1", "+5V", "C1", "1")

# Connecting U1 pin VO (Pin ID 3 -- Name VO) to #PWR3 pin +5V (Pin ID 1 -- Name +5V)
connect_pins("U1", "VO", "#PWR3", "+5V")

# Connecting C1 pin 1 (Pin ID 1 -- Name None) to U1 pin VI (Pin ID 1 -- Name VI)
connect_pins("C1", "1", "U1", "VI")

# Connecting C2 pin 1 (Pin ID 1 -- Name None) to U1 pin VO (Pin ID 3 -- Name VO)
connect_pins("C2", "1", "U1", "VO")

write_out_all_wires()