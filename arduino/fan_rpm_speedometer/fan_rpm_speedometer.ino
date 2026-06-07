/**
 * Fan RPM and Speed Measurement System
 * Arduino Uno Sketch
 * 
 * Hardware Connections:
 * 1. IR Sensor Module:
 *    - VCC  -> Arduino 5V
 *    - GND  -> Arduino GND
 *    - OUT  -> Arduino Digital Pin 2 (Interrupt 0)
 * 
 * 2. Servo Motor (SG90 or similar):
 *    - Red (VCC)    -> Arduino 5V (or external 5V if servo draws too much current)
 *    - Brown/Black  -> Arduino GND
 *    - Orange/Yellow -> Arduino Digital Pin 9 (PWM / Servo output)
 * 
 * Description:
 * This sketch measures the RPM of a rotating fan using a hardware interrupt on Pin 2.
 * It features robust software debouncing and digital filtering inside the interrupt to
 * ignore sensor noise and prevent false double-triggers. Every second, the sketch calculates 
 * the RPM based on a configurable blade count, maps it to a servo angle (functioning as an 
 * analog speedometer), and sends the statistics to the Flask backend via Serial.
 */

#include <Servo.h>

// --- Pin Definitions ---
const byte IR_SENSOR_PIN = 2; // Pin 2 supports external interrupts (INT0) on Uno
const byte SERVO_PIN = 9;     // PWM pin for SG90 Servo

// --- Servo Configuration ---
Servo speedometerServo;
const int SERVO_MIN_ANGLE = 0;   // Corresponding to 0 RPM
const int SERVO_MAX_ANGLE = 180; // Corresponding to MAX_RPM
const float MAX_RPM = 3000.0;    // Upper scale of the analog speedometer

// --- Measurement & Filtering Variables ---
volatile unsigned long pulseCount = 0;
volatile unsigned long lastPulseTime = 0; // In microseconds
const unsigned long DEBOUNCE_TIME_MICROS = 1500; // 1.5ms minimum between pulses (max ~40,000 pulses/min)

// RPM calculation variables
unsigned long lastRpmCalcTime = 0;
const unsigned int CALC_INTERVAL_MS = 1000; // Calculate RPM every 1 second
float currentRpm = 0.0;
float smoothedRpm = 0.0;
const float FILTER_BETA = 0.3; // Exponential smoothing filter coefficient (0.0 to 1.0)
                               // Higher beta = faster response, lower beta = smoother needle

// Slew rate limiting to reject physically impossible speed jumps (e.g. electrical noise spikes)
const float MAX_ACCEL_RPM_PER_SEC = 800.0; // Maximum RPM increase allowed per 1-second interval
const unsigned int MIN_PULSES_THRESHOLD = 2; // Discard isolated single pulses within the calculation window

// Fan configuration (Received from Flask backend via Serial)
volatile unsigned int bladeCount = 3; // Default to 3 blades

// --- Function Declarations ---
void handleSensorInterrupt();
void calculateRpm();
void updateServo();
void processSerialCommands();

void setup() {
  // Initialize Serial Communication at 9600 baud (standard, reliable rate)
  Serial.begin(9600);
  while (!Serial) {
    ; // Wait for serial port to connect (needed for native USB boards)
  }
  
  // Configure IR Sensor Pin
  // Using INPUT_PULLUP is highly recommended to prevent floating input if the 
  // sensor output is open-collector (common for photo-interrupters).
  pinMode(IR_SENSOR_PIN, INPUT_PULLUP);
  
  // Attach interrupt to Pin 2, triggering on FALLING edge (standard for active-low IR modules)
  attachInterrupt(digitalPinToInterrupt(IR_SENSOR_PIN), handleSensorInterrupt, FALLING);
  
  // Initialize Servo
  speedometerServo.attach(SERVO_PIN);
  speedometerServo.write(SERVO_MIN_ANGLE); // Start needle at 0
  
  // Output boot message
  Serial.println(F("{\"status\":\"booting\",\"msg\":\"Arduino Fan RPM System Initialized\"}"));
  lastRpmCalcTime = millis();
}

void loop() {
  // 1. Calculate RPM and print debug data at fixed intervals
  if (millis() - lastRpmCalcTime >= CALC_INTERVAL_MS) {
    calculateRpm();
    updateServo();
    lastRpmCalcTime = millis();
  }
  
  // 2. Check for configuration commands from Flask backend
  processSerialCommands();
}

/**
 * Interrupt Service Routine (ISR) for IR Sensor Pulse Detection.
 * Runs instantly when the IR sensor output transitions from HIGH to LOW.
 * Features:
 * 1. Temporal Debounce: Rejects events closer than 1.5ms.
 * 2. Digital Voting: Samples the pin 3 times with 2us spacing. The interrupt is only
 *    counted if all samples are LOW, filtering out transient electrical noise spikes.
 */
void handleSensorInterrupt() {
  unsigned long currentTime = micros();
  
  // 1. Software Debounce: Verify minimum time has passed since last pulse
  if (currentTime - lastPulseTime >= DEBOUNCE_TIME_MICROS) {
    
    // 2. Digital Voting Check: Sample the pin 3 times to ensure signal stability
    // This rejects microsecond-scale EMF spikes from servo motor currents or motor brushes.
    bool signalIsStable = true;
    for (int i = 0; i < 3; i++) {
      delayMicroseconds(2);
      if (digitalRead(IR_SENSOR_PIN) != LOW) {
        signalIsStable = false;
        break;
      }
    }
    
    if (signalIsStable) {
      pulseCount++;
      lastPulseTime = currentTime;
    }
  }
}

/**
 * Calculates the current RPM based on pulses received and active blade count.
 * Features:
 * 1. Low pulse thresholding: Ignores isolated single pulses (noise) when the fan is stopped.
 * 2. Slew Rate Limiting: Limits the maximum acceleration change to match real-world fan inertia.
 * 3. Exponential smoothing filter to stabilize speedometer readings.
 */
void calculateRpm() {
  // Temporarily disable interrupts while copying volatile values to prevent data race conditions
  noInterrupts();
  unsigned long rawPulses = pulseCount;
  pulseCount = 0; // Reset for next measurement window
  interrupts();
  
  // Calculate elapsed time precisely (in case loop execution is slightly delayed)
  unsigned long currentTime = millis();
  unsigned long elapsedMs = currentTime - lastRpmCalcTime;
  
  if (elapsedMs == 0) elapsedMs = 1; // Prevent division by zero
  
  // Noise Filter 1: Discard isolated single pulse spikes in the interval
  if (rawPulses < MIN_PULSES_THRESHOLD) {
    rawPulses = 0;
  }
  
  // RPM formula: (Pulses in interval / Blades) * (60,000ms / Interval in ms)
  float rawRpm = ((float)rawPulses / (float)bladeCount) * (60000.0 / (float)elapsedMs);
  
  if (rawRpm < 0) rawRpm = 0;
  
  // Noise Filter 2: Slew Rate Limiting (Inertia check)
  // Clamp physically impossible acceleration jumps (e.g. from 0 to 1000 RPM in 1 second)
  if (smoothedRpm == 0.0) {
    // If starting from stopped, cap the first second jump to a reasonable startup value
    if (rawRpm > 400.0) {
      rawRpm = 400.0;
    }
  } else {
    // If already spinning, limit the increase per second
    float maxRpmLimit = smoothedRpm + MAX_ACCEL_RPM_PER_SEC;
    if (rawRpm > maxRpmLimit) {
      rawRpm = maxRpmLimit;
    }
  }
  
  currentRpm = rawRpm;
  
  // Apply exponential smoothing filter:
  // smoothedRpm = (1 - beta) * smoothedRpm + beta * currentRpm
  smoothedRpm = ((1.0 - FILTER_BETA) * smoothedRpm) + (FILTER_BETA * currentRpm);
  
  // Prevent slight float creeping near zero
  if (smoothedRpm < 5.0) {
    smoothedRpm = 0.0;
  }
}

/**
 * Maps the smoothed RPM value to a corresponding servo angle and updates the servo.
 * Ensures smooth transition and stays within mechanical servo bounds.
 */
void updateServo() {
  // Constrain RPM to the speedometer scale limits
  float boundedRpm = constrain(smoothedRpm, 0.0, MAX_RPM);
  
  // Map RPM to Servo Angle in reverse to achieve Clockwise rotation:
  // 0 RPM -> 180 deg (far left), MAX_RPM -> 0 deg (far right)
  int servoAngle = map(boundedRpm, 0.0, MAX_RPM, SERVO_MAX_ANGLE, SERVO_MIN_ANGLE);
  
  // Write angle to SG90 servo motor
  speedometerServo.write(servoAngle);
  
  // Send formatted JSON to Serial port for Flask backend parsing.
  // Using JSON guarantees robust parsing on the backend.
  Serial.print(F("{\"rpm\":"));
  Serial.print(smoothedRpm, 1);
  Serial.print(F(",\"pulses\":"));
  Serial.print(pulseCount); // Print current pulses accumulated so far in the new cycle
  Serial.print(F(",\"angle\":"));
  Serial.print(servoAngle);
  Serial.print(F(",\"blades\":"));
  Serial.print(bladeCount);
  Serial.println(F("}"));
}

/**
 * Parses serial commands sent from the Flask web interface.
 * Format expected: "BLADES:N" where N is an integer (e.g. "BLADES:4").
 */
void processSerialCommands() {
  if (Serial.available() > 0) {
    String inputString = Serial.readStringUntil('\n');
    inputString.trim(); // Clean whitespace and newlines
    
    if (inputString.startsWith("BLADES:")) {
      String valueStr = inputString.substring(7);
      int newBladeCount = valueStr.toInt();
      
      // Validate configuration: blade count must be at least 1
      if (newBladeCount >= 1 && newBladeCount <= 20) {
        noInterrupts();
        bladeCount = newBladeCount;
        interrupts();
        
        // Print confirmation back to serial
        Serial.print(F("{\"status\":\"config_updated\",\"blades\":"));
        Serial.print(bladeCount);
        Serial.println(F("}"));
      } else {
        Serial.println(F("{\"status\":\"error\",\"msg\":\"Invalid blade count (1-20)\"}"));
      }
    }
  }
}
