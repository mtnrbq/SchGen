# Auto-generated schematic symbols
import sys
import os

# Get project path and import kicad schematic interface
PROJECT_PATH = os.environ['PROJECT_PATH']
sys.path.append(PROJECT_PATH)
from modules.kicad_sch_interface import *
set_schematic_filename(r"D:\SchGen\usb_b_test\usb_b_test.kicad_sch")

### Placing center symbol 1 : Connector:USB_B###

add_schematic_symbol(symbol_lib="Connector", symbol_name="USB_B", pos_x=150.0, pos_y=110.0, reference="USB1", value="USB_B", rotation=0, mirror="None")

### Placing other symbols in the Schematic with respect to the center symbol 1###


### Placing all global labels in the Schematic and connect them to the neighbor pin ###

# Add label D+ next to USB1 pin D+ 
x_USB1_3, y_USB1_3 = get_pin_location(symbol_ref="USB1", pin_name="D+")
add_label(label_pos=[x_USB1_3+(-13), y_USB1_3+(0)], label_text="D+", label_ref="D+_0", label_type="input", text_orient="left")
# Connecting Label D+ label_id:0 to USB1 pin D+ (Pin ID 3 -- Name D+)
connect_pins("D+_0", "1", "USB1", "D+")
# Add label D- next to USB1 pin D- 
x_USB1_2, y_USB1_2 = get_pin_location(symbol_ref="USB1", pin_name="D-")
add_label(label_pos=[x_USB1_2+(-13), y_USB1_2+(0)], label_text="D-", label_ref="D-_0", label_type="input", text_orient="left")
# Connecting Label D- label_id:0 to USB1 pin D- (Pin ID 2 -- Name D-)
connect_pins("D-_0", "1", "USB1", "D-")

### Connecting all wires in the Schematic ###


write_out_all_wires()