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

        // Intercept Sydney WebSocket URL (substrate / Chathub / Sydney)
        if (typeof url === 'string' && (url.includes('/m365Copilot/Chathub') || url.includes('substrate.office.com') || url.includes('/sydney/ChatHub') || (url.includes('Chathub') && url.includes('access_token')))) {
            const origSend = ws.send;
            ws.send = function(data) {
                try {
                    if (window.__onSydneyFrameIntercepted) {
                        window.__onSydneyFrameIntercepted({ data: String(data) });
                    }
                } catch (e) {}
                return origSend.apply(this, arguments);
            };

            ws.addEventListener('message', (event) => {
                try {
                    if (window.__onSydneyRecvFrame) {
                        window.__onSydneyRecvFrame({ data: String(event.data) });
                    }
                } catch (e) {}
            });

            try {
                // Parse access_token from query string
                const urlObj = new URL(url);
                const token = urlObj.searchParams.get('access_token');

                if (token) {
                    // Extract MSAL refresh token from localStorage or sessionStorage if available
                    let refreshToken = null;
                    const storages = [localStorage, sessionStorage];
                    for (const storage of storages) {
                        try {
                            if (!storage) continue;
                            for (let i = 0; i < storage.length; i++) {
                                const key = storage.key(i);
                                if (key && (key.toLowerCase().includes('refreshtoken') || key.toLowerCase().includes('refresh_token') || key.toLowerCase().includes('credential'))) {
                                    const val = storage.getItem(key);
                                    try {
                                        const parsed = JSON.parse(val);
                                        if (parsed && parsed.secret && (parsed.credentialType === 'RefreshToken' || !parsed.credentialType)) {
                                            refreshToken = parsed.secret;
                                            break;
                                        }
                                    } catch (e) {}
                                }
                            }
                        } catch (e) {}
                        if (refreshToken) break;
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

    // Intercept fetch calls to substrate or sydney
    const origFetch = window.fetch;
    window.fetch = async function(...args) {
        const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url ? args[0].url : '');
        if (url && (url.includes('substrate') || url.includes('sydney') || url.includes('create') || url.includes('conversation') || url.includes('token'))) {
            try {
                if (window.__onSydneyFrameIntercepted) {
                    window.__onSydneyFrameIntercepted({ data: `[FETCH] ${url}` });
                }
            } catch(e) {}
        }
        return origFetch.apply(this, args);
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
