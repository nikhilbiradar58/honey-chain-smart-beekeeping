LIVE STATUS FIX

The top-right status is now about backend connectivity, not whether the weather provider answered.
- BACKEND ONLINE · WEATHER LIVE = Python backend is running and recent external weather data exists.
- BACKEND ONLINE · WAITING FOR WEATHER = Python backend is running, but the external weather provider could not be reached yet.
- SENSOR LIVE = a real sensor telemetry record was received within the last 10 minutes.

If the screen says WAITING FOR WEATHER, check that the PC has internet access and that Python is allowed through Windows Firewall/antivirus. The AI tag can still show LLM when your OpenAI API key is configured.
