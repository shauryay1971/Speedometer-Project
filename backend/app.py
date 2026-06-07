import os
import sys
import time
import json
import threading
from flask import Flask, render_template, jsonify, request

# Try to import serial (from pyserial). If not installed, we can fall back to mockup mode.
try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

app = Flask(__name__)

# --- Configuration & State ---
SERIAL_PORT = "/dev/cu.usbmodemFX2348N1"
BAUD_RATE = 9600

# Global state dictionary with a Lock for thread-safe access
system_state = {
    "rpm": 0.0,
    "pulses": 0,
    "servo_angle": 0,
    "blades": 3,
    "radius": 10.0,    # Default radius
    "unit": "cm",      # 'cm' or 'm'
    "arduino_connected": False,
    "is_mock_mode": False
}
state_lock = threading.Lock()

# Global Serial connection holder
ser_conn = None
serial_lock = threading.Lock()

# Command queue to send to Arduino from backend thread
commands_to_send = []
cmd_lock = threading.Lock()

def get_system_state():
    """Retrieve a copy of system state in a thread-safe manner."""
    with state_lock:
        return dict(system_state)

def update_system_state(**kwargs):
    """Update system state variables in a thread-safe manner."""
    with state_lock:
        for key, value in kwargs.items():
            if key in system_state:
                system_state[key] = value

def queue_arduino_command(cmd_str):
    """Queue a command to be written to the serial interface."""
    with cmd_lock:
        commands_to_send.append(cmd_str)

def run_serial_reader():
    """
    Background thread that manages the serial interface:
    - Automatically handles connection and graceful reconnection.
    - Continuously reads lines from the Arduino.
    - Decodes and parses JSON telemetry data.
    - Sends queued configuration commands back to the Arduino.
    - Falls back to simulation/mock mode if no Arduino is connected.
    """
    global ser_conn
    print(f"[*] Starting Serial Reader thread on port: {SERIAL_PORT}")
    
    last_mock_update = time.time()
    mock_direction = 1
    mock_rpm = 0.0
    
    while True:
        if not SERIAL_AVAILABLE:
            # Pyserial not installed, run simulation
            update_system_state(arduino_connected=False, is_mock_mode=True)
            mock_rpm, mock_direction, last_mock_update = simulate_data(mock_rpm, mock_direction, last_mock_update)
            time.sleep(0.5)
            continue
        
        try:
            # Check if port exists or connect
            with serial_lock:
                if ser_conn is None:
                    # Check if port exists in available ports
                    ports = [p.device for p in serial.tools.list_ports.comports()]
                    if SERIAL_PORT in ports:
                        print(f"[*] Port {SERIAL_PORT} found. Attempting connection...")
                        ser_conn = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.5)
                        # Flush buffers
                        ser_conn.reset_input_buffer()
                        ser_conn.reset_output_buffer()
                        
                        # Wait for Arduino bootloader (reboot occurs on serial open)
                        time.sleep(2.0)
                        
                        # Write initial configuration
                        state = get_system_state()
                        ser_conn.write(f"BLADES:{state['blades']}\n".encode('utf-8'))
                        
                        update_system_state(arduino_connected=True, is_mock_mode=False)
                        print(f"[+] Connected to Arduino Uno on {SERIAL_PORT}")
                    else:
                        # Port not found, trigger mock simulation mode so UI stays active
                        update_system_state(arduino_connected=False, is_mock_mode=True)
                        mock_rpm, mock_direction, last_mock_update = simulate_data(mock_rpm, mock_direction, last_mock_update)
                        time.sleep(1.0)
                        continue
            
            # Read telemetry from Arduino
            line = ""
            with serial_lock:
                if ser_conn and ser_conn.is_open:
                    if ser_conn.in_waiting > 0:
                        try:
                            line = ser_conn.readline().decode('utf-8', errors='ignore').strip()
                        except Exception as e:
                            print(f"[!] Error reading serial line: {e}")
                            ser_conn.close()
                            ser_conn = None
                            update_system_state(arduino_connected=False)
                            continue
            
            # Parse line if not empty
            if line:
                # Expecting JSON string like: {"rpm":1200.0,"pulses":0,"angle":72,"blades":3}
                try:
                    data = json.loads(line)
                    if "rpm" in data:
                        update_system_state(
                            rpm=float(data["rpm"]),
                            pulses=int(data.get("pulses", 0)),
                            servo_angle=int(data.get("angle", 0)),
                            blades=int(data.get("blades", system_state["blades"]))
                        )
                except json.JSONDecodeError:
                    # If not JSON, output raw line (helps with debug messages)
                    print(f"[Arduino Raw Debug]: {line}")
            
            # Send queued commands (e.g. BLADES config) to Arduino
            with cmd_lock:
                cmds = list(commands_to_send)
                commands_to_send.clear()
                
            if cmds:
                with serial_lock:
                    if ser_conn and ser_conn.is_open:
                        for cmd in cmds:
                            print(f"[*] Sending Serial Command to Arduino: {cmd.strip()}")
                            try:
                                ser_conn.write(cmd.encode('utf-8'))
                            except Exception as e:
                                print(f"[!] Serial write error: {e}")
                                
            time.sleep(0.05) # Tiny rest to prevent high CPU utilization
            
        except (serial.SerialException, OSError) as e:
            print(f"[!] Serial communication lost: {e}")
            with serial_lock:
                if ser_conn:
                    try:
                        ser_conn.close()
                    except:
                        pass
                    ser_conn = None
            update_system_state(arduino_connected=False)
            time.sleep(2.0) # Wait before attempting reconnection

def simulate_data(current_mock_rpm, direction, last_update_time):
    """
    Simulates fan telemetry when no Arduino is connected.
    Smoothly ramps RPM up and down to show animations on the dashboard.
    """
    now = time.time()
    elapsed = now - last_update_time
    
    # Update every 0.5s or more
    if elapsed >= 0.5:
        # Configuration parameters
        state = get_system_state()
        
        # Adjust mock RPM
        step = 120.0 * elapsed
        if direction > 0:
            current_mock_rpm += step
            if current_mock_rpm >= 1800.0:
                direction = -1
        else:
            current_mock_rpm -= step
            if current_mock_rpm <= 0.0:
                current_mock_rpm = 0.0
                direction = 1
                
        # Angle mapping (0 - 3000 RPM -> 0 - 180 degrees)
        mock_angle = int((min(current_mock_rpm, 3000.0) / 3000.0) * 180.0)
        
        # Random pulses count based on mock RPM
        # RPM = (pulses / blades) * 60  => pulses = (RPM * blades) / 60
        sim_pulses = int((current_mock_rpm * state["blades"]) / 60.0)
        
        update_system_state(
            rpm=round(current_mock_rpm, 1),
            pulses=sim_pulses,
            servo_angle=mock_angle
        )
        return current_mock_rpm, direction, now
        
    return current_mock_rpm, direction, last_update_time

# --- API Routes ---

@app.route('/')
def index():
    """Render the dashboard page."""
    return render_template('index.html')

@app.route('/api/data', methods=['GET'])
def get_data():
    """
    Returns the latest sensor values, speed calculations, and configuration.
    Calculates blade tip speed in m/s and km/h dynamically using exact formulas.
    """
    state = get_system_state()
    
    # 1. Extract inputs
    rpm = state["rpm"]
    radius = state["radius"]
    unit = state["unit"]
    
    # 2. Convert radius to meters
    # No assumptions made; explicit conversion based on selected unit
    if unit == "cm":
        radius_m = radius / 100.0
    else:
        radius_m = radius
        
    # 3. Calculate Speed
    # Circumference C = 2 * pi * r
    # Distance per minute D = RPM * C
    # Speed in m/s: v = (RPM * 2 * pi * r) / 60
    # Speed in km/h: v_kmh = (RPM * 2 * pi * r * 60) / 1000 = v * 3.6
    pi = 3.141592653589793
    circumference = 2.0 * pi * radius_m
    
    speed_ms = (rpm * circumference) / 60.0
    speed_kmh = speed_ms * 3.6
    
    response_data = {
        "rpm": rpm,
        "pulses": state["pulses"],
        "servo_angle": state["servo_angle"],
        "blades": state["blades"],
        "radius": radius,
        "unit": unit,
        "circumference_m": round(circumference, 4),
        "speed_ms": round(speed_ms, 2),
        "speed_kmh": round(speed_kmh, 2),
        "arduino_connected": state["arduino_connected"],
        "is_mock_mode": state["is_mock_mode"],
        "serial_port": SERIAL_PORT
    }
    
    return jsonify(response_data)

@app.route('/api/config', methods=['POST'])
def update_config():
    """
    Updates the configuration (blades, radius, unit) from UI requests.
    Queues serial commands to notify Arduino when blades count changes.
    """
    req_data = request.get_json() or {}
    
    blades = req_data.get("blades")
    radius = req_data.get("radius")
    unit = req_data.get("unit")
    
    updates = {}
    
    if blades is not None:
        try:
            blades_val = int(blades)
            if 1 <= blades_val <= 20:
                updates["blades"] = blades_val
                # Queue a command to send to Arduino over Serial
                queue_arduino_command(f"BLADES:{blades_val}\n")
        except ValueError:
            pass
            
    if radius is not None:
        try:
            radius_val = float(radius)
            if radius_val > 0:
                updates["radius"] = radius_val
        except ValueError:
            pass
            
    if unit in ["cm", "m"]:
        updates["unit"] = unit
        
    if updates:
        update_system_state(**updates)
        return jsonify({"status": "success", "updated": list(updates.keys())})
        
    return jsonify({"status": "error", "message": "No valid parameters provided"}), 400

if __name__ == '__main__':
    # Start Serial Reader Thread as a background daemon
    serial_thread = threading.Thread(target=run_serial_reader, daemon=True)
    serial_thread.start()
    
    # Run Flask application on port 5001 (to avoid default 5000 macOS AirPlay conflicts)
    print("[*] Launching Flask Dashboard on http://localhost:5001")
    app.run(host='0.0.0.0', port=5001, debug=False)
