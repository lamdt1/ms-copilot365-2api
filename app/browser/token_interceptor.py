WS_INTERCEPT_SCRIPT = """
(() => {
    // Save original WebSocket constructor
    const OrigWebSocket = window.WebSocket;

    // Override WebSocket
    window.WebSocket = function(url, protocols) {
        let ws;
        if (protocols) {
            ws = new OrigWebSocket(url, protocols);
        } else {
            ws = new OrigWebSocket(url);
        }

        // Intercept Sydney WebSocket URL
        if (typeof url === 'string' && url.includes('wss://substrate.office.com/m365Copilot/Chathub')) {
            try {
                // Parse access_token from query string
                const urlObj = new URL(url);
                const token = urlObj.searchParams.get('access_token');

                if (token) {
                    // Extract MSAL refresh token from localStorage if available
                    let refreshToken = null;
                    for (let i = 0; i < localStorage.length; i++) {
                        const key = localStorage.key(i);
                        if (key && key.includes('refresh_token')) {
                            const val = localStorage.getItem(key);
                            try {
                                const parsed = JSON.parse(val);
                                if (parsed.secret) {
                                    refreshToken = parsed.secret;
                                    break;
                                }
                            } catch (e) {}
                        }
                    }

                    // Call bound python function exposed via Playwright
                    if (window.__onSydneyTokenIntercepted) {
                        window.__onSydneyTokenIntercepted({
                            url: url,
                            access_token: token,
                            refresh_token: refreshToken
                        });
                    }
                }
            } catch (err) {
                console.error('Interceptor Error:', err);
            }
        }
        return ws;
    };

    // Forward static properties
    for (let prop in OrigWebSocket) {
        if (OrigWebSocket.hasOwnProperty(prop)) {
            window.WebSocket[prop] = OrigWebSocket[prop];
        }
    }
    window.WebSocket.prototype = OrigWebSocket.prototype;
})();
"""
