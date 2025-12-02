# Fix for ICG Portal Iframe Embedding Issue

## Problem
When the ICG portal (running on `localhost:4200`) tries to embed the Open edX dashboard using the generated link (`http://52.52.41.225:8000/access/...?iframe=1`), it shows an error because:
1. The `/access/` endpoint was returning JSON instead of HTML when no session token existed
2. CORS headers weren't properly set to allow embedding from localhost
3. X-Frame-Options headers were blocking iframe embedding

## Solution

### Changes Made to `/access/` Endpoint

1. **Check iframe parameter FIRST**: Now checks for `iframe=1` or `embedded=1` parameter before checking format
2. **Always return HTML for iframe requests**: When `iframe=1` is present, always returns HTML (even if no token exists yet)
3. **Use dashboard-proxy endpoint**: The iframe HTML now loads `/dashboard-proxy/{link_id}` which properly handles sessions
4. **Proper CORS headers**: Added headers to allow embedding from any origin:
   - `X-Frame-Options: ALLOWALL`
   - `Content-Security-Policy: frame-ancestors *`
   - `Access-Control-Allow-Origin: *`
   - `Access-Control-Allow-Methods: GET, POST, OPTIONS`
   - `Access-Control-Allow-Headers: *`

### Changes Made to `/dashboard-proxy/` Endpoint

1. **Updated CORS headers**: Changed from `SAMEORIGIN` to `ALLOWALL` to allow embedding from any origin
2. **Added CORS headers**: Same headers as `/access/` endpoint

## How It Works Now

1. **ICG Portal** (localhost:4200) calls `/generate-link` → Returns: `{"link": "http://52.52.41.225:8000/access/{link_id}"}`

2. **ICG Portal** adds `?iframe=1&embedded=1` and embeds in iframe:
   ```html
   <iframe src="http://52.52.41.225:8000/access/{link_id}?iframe=1&embedded=1"></iframe>
   ```

3. **FastAPI `/access/` endpoint**:
   - Detects `iframe=1` parameter
   - Registers/logs in user if needed
   - Returns HTML with iframe that loads `/dashboard-proxy/{link_id}`
   - Sets proper CORS headers

4. **FastAPI `/dashboard-proxy/` endpoint**:
   - Uses stored session token
   - Fetches dashboard from Open edX
   - Returns HTML with proper CORS headers
   - Dashboard displays in iframe

## Testing

After deploying, test the flow:

1. **From ICG Portal** (localhost:4200):
   - Navigate to Training & Simulations page
   - Should see Open edX dashboard loading in iframe
   - No CORS errors in browser console
   - Dashboard should display properly

2. **Check Browser Console**:
   - No CORS errors
   - No X-Frame-Options blocking errors
   - Iframe loads successfully

## Configuration

Ensure these environment variables are set in `docker-compose.yml`:

```yaml
environment:
  - FASTAPI_PUBLIC_BASE_URL=http://52.52.41.225:8000  # Public server URL
  - OPENEDX_API_BASE=http://lms:18000                  # Internal Docker network
  - OPENEDX_DASHBOARD_URL=http://lms:18000/dashboard  # Internal Docker network
```

## Files Modified

- `main.py`: Updated `/access/` and `/dashboard-proxy/` endpoints to handle iframe requests properly


