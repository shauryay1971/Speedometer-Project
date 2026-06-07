/**
 * Aerovane Dashboard Logic
 * Client-Side Telemetry Polling, Gauge Animations & Configuration Control
 */

document.addEventListener('DOMContentLoaded', () => {
    // --- UI Element Selectors ---
    const connectionStatus = document.getElementById('connection-status');
    const portName = document.getElementById('port-name');
    const rpmValue = document.getElementById('rpm-value');
    const speedometerNeedle = document.getElementById('speedometer-needle');
    const gaugeProgress = document.getElementById('gauge-progress');
    
    // Speed (km/h) Gauge Selectors
    const speedNeedle = document.getElementById('speed-needle');
    const speedGaugeProgress = document.getElementById('speed-gauge-progress');
    const speedValue = document.getElementById('speed-value');
    
    const servoHorn = document.getElementById('servo-horn');
    const servoAngleText = document.getElementById('servo-angle-text');
    const speedKmh = document.getElementById('speed-kmh');
    const speedMs = document.getElementById('speed-ms');
    const pulsesValue = document.getElementById('pulses-value');
    const circumferenceValue = document.getElementById('circumference-value');
    const systemModeBadge = document.getElementById('system-mode-badge');
    const toast = document.getElementById('toast');
    const toastMessage = document.getElementById('toast-message');
    
    // Config Form Inputs
    const configForm = document.getElementById('config-form');
    const bladesInput = document.getElementById('blades-input');
    const radiusInput = document.getElementById('radius-input');
    const unitSelect = document.getElementById('unit-select');

    // State trackers
    let isFirstLoad = true;
    const MAX_RPM = 3000;
    const GAUGE_CIRCUMFERENCE = 377; // Matches the stroke-dasharray in HTML

    /**
     * Shows a temporary toast message on the UI.
     */
    function showToast(message, isError = false) {
        toastMessage.textContent = message;
        if (isError) {
            toast.style.borderColor = 'hsl(var(--color-danger))';
        } else {
            toast.style.borderColor = 'hsl(var(--color-primary))';
        }
        
        toast.classList.remove('hidden');
        
        // Hide after 3 seconds
        setTimeout(() => {
            toast.classList.add('hidden');
        }, 3000);
    }

    /**
     * Formats speed values to be visually consistent (always one decimal place)
     */
    function formatValue(val) {
        return parseFloat(val).toFixed(1);
    }

    /**
     * Polls the backend API for real-time telemetry and updates the dashboard.
     */
    async function fetchTelemetry() {
        try {
            const response = await fetch('/api/data');
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            const data = await response.json();
            
            // 1. Update Connection Status Indicators
            updateConnectionUI(data);
            
            // 2. Update Numerical Readings
            rpmValue.textContent = Math.round(data.rpm);
            speedKmh.textContent = formatValue(data.speed_kmh);
            speedMs.textContent = formatValue(data.speed_ms);
            pulsesValue.textContent = data.pulses;
            circumferenceValue.textContent = `${data.circumference_m} m`;
            
            // 3. RPM Speedometer Needle & Arc Progress Animation
            // RPM is scaled from 0 to MAX_RPM (3000)
            const rpmFraction = Math.min(data.rpm, MAX_RPM) / MAX_RPM;
            
            // Needle rotates in a 270-degree sweep from -135deg to +135deg
            const needleAngle = -135 + (rpmFraction * 270);
            speedometerNeedle.style.transform = `translate(-50%, 0) rotate(${needleAngle}deg)`;
            
            // Circular arc progress (stroke-dashoffset from 377 (empty) to 0 (full))
            const dashOffset = GAUGE_CIRCUMFERENCE - (rpmFraction * GAUGE_CIRCUMFERENCE);
            gaugeProgress.style.strokeDashoffset = dashOffset;
            
            // 3b. Velocity Speedometer Needle & Arc Progress (0 to 100 km/h)
            const speedFraction = Math.min(data.speed_kmh, 100) / 100;
            const speedNeedleAngle = -135 + (speedFraction * 270);
            speedNeedle.style.transform = `translate(-50%, 0) rotate(${speedNeedleAngle}deg)`;
            
            const speedDashOffset = GAUGE_CIRCUMFERENCE - (speedFraction * GAUGE_CIRCUMFERENCE);
            speedGaugeProgress.style.strokeDashoffset = speedDashOffset;
            speedValue.textContent = formatValue(data.speed_kmh);
            
            // 4. Virtual Servo Synchronization
            // Servo angle comes directly from Arduino mapping (0 - 180 degrees)
            servoAngleText.textContent = data.servo_angle;
            // Physical servo now maps: 0 RPM -> 180 deg (far left), MAX_RPM -> 0 deg (far right)
            // Visual rotation: -180 deg points horizontal left, 0 deg points horizontal right.
            // Rotating clockwise: visual_angle = -servo_angle (so 180 -> -180, 0 -> 0)
            const hornRotation = -data.servo_angle;
            servoHorn.style.transform = `rotate(${hornRotation}deg)`;

            // 5. Initialize config inputs on initial load to match server defaults
            if (isFirstLoad) {
                bladesInput.value = data.blades;
                radiusInput.value = data.radius;
                unitSelect.value = data.unit;
                isFirstLoad = false;
            }
            
        } catch (error) {
            console.error('Telemetry fetch error:', error);
            // Show disconnected UI state
            setOfflineUI();
        }
    }

    /**
     * Updates the status bar, badge, and color schemes depending on the state of the Arduino connection.
     */
    function updateConnectionUI(data) {
        // Set Port Info
        portName.innerHTML = `<i class="fa-solid fa-microchip"></i> Port: <code>${data.serial_port}</code>`;

        if (data.arduino_connected) {
            // Arduino Connected successfully
            connectionStatus.className = 'status-pill connected';
            connectionStatus.querySelector('.status-text').textContent = 'Arduino Connected';
            
            systemModeBadge.textContent = 'Hardware Mode';
            systemModeBadge.className = 'mode-badge hardware-badge';
        } else if (data.is_mock_mode) {
            // Emulating data via mock mode
            connectionStatus.className = 'status-pill mock';
            connectionStatus.querySelector('.status-text').textContent = 'Simulating (Mock)';
            
            systemModeBadge.textContent = 'Simulation Mode';
            systemModeBadge.className = 'mode-badge mock-badge';
        } else {
            // Completely offline / disconnected
            setOfflineUI();
        }
    }

    /**
     * Sets the UI into offline mode when connection is completely lost.
     */
    function setOfflineUI() {
        connectionStatus.className = 'status-pill disconnected';
        connectionStatus.querySelector('.status-text').textContent = 'Arduino Disconnected';
        
        systemModeBadge.textContent = 'System Offline';
        systemModeBadge.className = 'mode-badge mock-badge';
        
        // Return speedometers and servo to zero
        speedometerNeedle.style.transform = `translate(-50%, 0) rotate(-135deg)`;
        gaugeProgress.style.strokeDashoffset = GAUGE_CIRCUMFERENCE;
        
        speedNeedle.style.transform = `translate(-50%, 0) rotate(-135deg)`;
        speedGaugeProgress.style.strokeDashoffset = GAUGE_CIRCUMFERENCE;
        speedValue.textContent = '0.0';
        
        servoHorn.style.transform = `rotate(-180deg)`; // 0 RPM is horizontal left (-180deg)
        servoAngleText.textContent = '180';
        rpmValue.textContent = '0';
        speedKmh.textContent = '0.0';
        speedMs.textContent = '0.0';
        pulsesValue.textContent = '0';
    }

    /**
     * Submits updated blade parameters and radius configuration to the backend server.
     */
    async function submitConfig() {
        const payload = {
            blades: parseInt(bladesInput.value),
            radius: parseFloat(radiusInput.value),
            unit: unitSelect.value
        };

        // Validate payload values locally first
        if (isNaN(payload.blades) || payload.blades < 1 || payload.blades > 20) {
            showToast('Blade count must be between 1 and 20', true);
            return;
        }
        if (isNaN(payload.radius) || payload.radius <= 0) {
            showToast('Radius must be a positive number', true);
            return;
        }

        try {
            const response = await fetch('/api/config', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                throw new Error('Failed to update config');
            }

            const result = await response.json();
            if (result.status === 'success') {
                showToast('Configuration applied and synced successfully!');
                // Force a poll update immediately to sync values
                fetchTelemetry();
            } else {
                showToast(result.message || 'Error updating configuration', true);
            }
        } catch (error) {
            console.error('Config update error:', error);
            showToast('Failed to connect to backend server for configuration update', true);
        }
    }

    // --- Event Listeners ---
    configForm.addEventListener('submit', (e) => {
        e.preventDefault();
        submitConfig();
    });

    // Also auto-apply configurations if input loses focus (blur event)
    bladesInput.addEventListener('blur', submitConfig);
    radiusInput.addEventListener('blur', submitConfig);
    unitSelect.addEventListener('change', submitConfig);

    // --- Dashboard Initialisation & Loop ---
    fetchTelemetry(); // Initial fetch
    
    // Poll the backend every 1000ms (1 second) for real-time updates
    setInterval(fetchTelemetry, 1000);
});
