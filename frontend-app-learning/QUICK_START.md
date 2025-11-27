# Learning MFE - Quick Start Guide

## 🚀 Start in 3 Steps

### 1️⃣ Make sure LMS is running
```bash
# LMS should be accessible at:
http://localhost:18000
```

### 2️⃣ Start the Learning MFE
```bash
cd "/home/suriya-vcw/Desktop/manual build/frontend-app-learning"
./start-learning.sh
```

### 3️⃣ Access courses
```bash
# Login to LMS:
http://localhost:18000/login

# Click on any course from dashboard
# It will open in the Learning MFE at:
http://localhost:2000
```

---

## 📋 Important Commands

```bash
# First time setup
./setup_learning_mfe.sh

# Start Learning MFE
./start-learning.sh

# Test integration
./test-integration.sh

# Stop MFE
# Press Ctrl+C in the terminal
```

---

## 🔗 URLs Reference

| What | URL |
|------|-----|
| Learning MFE | http://localhost:2000 |
| LMS | http://localhost:18000 |
| Studio | http://localhost:18010 |

---

## ⚠️ Common Issues

### Issue: "Port already in use"
```bash
# Find and kill the process
lsof -i :2000
kill -9 <PID>
```

### Issue: CORS errors
```bash
# Restart LMS
cd "/home/suriya-vcw/Desktop/manual build/edx-platform"
./stop-lms.sh
./start-lms.sh
```

### Issue: Login problems
- Clear browser cookies
- Login again to LMS first
- Then access course

---

## 📚 Full Documentation

- **INTEGRATION_GUIDE.md** - Complete guide with troubleshooting
- **INTEGRATION_SUMMARY.md** - What was changed
- **README.rst** - Official documentation

---

## ✅ Verify It's Working

1. Run the test script:
```bash
./test-integration.sh
```

2. All tests should pass ✓

3. Start the MFE:
```bash
./start-learning.sh
```

4. Open browser: http://localhost:2000

---

That's it! Happy learning! 🎓

