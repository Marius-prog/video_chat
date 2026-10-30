# Video Chat Application - Military-Grade Security Edition

A secure, enterprise-ready group video calling platform built with Django and Agora WebRTC.

This project started as **MyChat**, a group video calling application using the Agora Web SDK with a Django backend, from the [divanov11/mychat](https://github.com/divanov11/mychat) tutorial project. One difference from that tutorial: Agora credentials are no longer pasted into `views.py` and `streams.js`; they are read from `.env` (see Configuration below).

## 🛡️ Security Features

This application implements **military-grade security** with comprehensive protection against common web vulnerabilities:

- ✅ **No Hardcoded Secrets** - All credentials managed via environment variables
- ✅ **CSRF Protection** - Full CSRF token validation on all state-changing operations
- ✅ **Rate Limiting** - Protection against abuse and DoS attacks
- ✅ **Input Validation** - Comprehensive server-side and client-side validation
- ✅ **XSS Prevention** - HTML escaping and Content Security Policy
- ✅ **HTTPS Enforcement** - Secure transport with HSTS headers
- ✅ **Session Security** - HttpOnly, Secure, SameSite cookies
- ✅ **Authorization Controls** - Session-based ownership validation
- ✅ **Security Logging** - Comprehensive audit trail
- ✅ **Security Headers** - CSP, X-Frame-Options, X-Content-Type-Options

See [SECURITY.md](SECURITY.md) for complete security documentation.

## 🚀 Quick Start

### Prerequisites

- Python 3.7+
- Agora account ([sign up here](https://www.agora.io/))
- Git

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/Marius-prog/video_chat.git
   cd video_chat
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment**
   ```bash
   cp .env.example .env
   nano .env  # Edit with your values
   ```

   **Required Environment Variables:**
   ```env
   SECRET_KEY=your-django-secret-key
   DEBUG=False
   ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
   AGORA_APP_ID=your-agora-app-id
   AGORA_APP_CERTIFICATE=your-agora-app-certificate
   ```

5. **Run migrations**
   ```bash
   python manage.py migrate
   ```

6. **Create superuser**
   ```bash
   python manage.py createsuperuser
   ```

7. **Collect static files**
   ```bash
   python manage.py collectstatic
   ```

8. **Run development server**
   ```bash
   python manage.py runserver
   ```

   **⚠️ For Development Only:** Set `DEBUG=True` in .env for local development

## 🔧 Configuration

### Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `SECRET_KEY` | Django secret key | Yes | - |
| `DEBUG` | Debug mode | No | False |
| `ALLOWED_HOSTS` | Comma-separated hosts | Yes | localhost,127.0.0.1 |
| `AGORA_APP_ID` | Agora application ID | Yes | - |
| `AGORA_APP_CERTIFICATE` | Agora app certificate | Yes | - |
| `SECURE_SSL_REDIRECT` | Force HTTPS | No | True (if not DEBUG) |
| `SESSION_COOKIE_SECURE` | Secure session cookies | No | True (if not DEBUG) |
| `CSRF_COOKIE_SECURE` | Secure CSRF cookies | No | True (if not DEBUG) |

See `.env.example` for all available options.

### Generating Secret Key

```bash
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

### Agora Setup

1. Sign up at [Agora.io](https://www.agora.io/)
2. Create a new project
3. Enable **App Certificate** in project settings
4. Copy App ID and App Certificate to `.env`

If you are unsure where these live in the Agora console, [this part of the original tutorial video](https://youtu.be/HX6AM_1-jNM?t=88) shows it.

## 📖 Usage

1. Navigate to the lobby page
2. Enter a room name (1-64 characters, alphanumeric)
3. Enter your display name (2-100 characters)
4. Click "Join Stream"
5. Grant camera and microphone permissions
6. Start chatting!

### Controls

- 🎤 **Microphone** - Toggle audio on/off
- 📹 **Camera** - Toggle video on/off
- 🚪 **Leave** - Exit the room

## 🏗️ Architecture

### Backend (Django)

```
mychat/
├── settings.py    # Security-hardened configuration
├── urls.py        # URL routing
└── wsgi.py        # WSGI application

base/
├── models.py      # RoomMember model
├── views.py       # Secure API endpoints with rate limiting
├── urls.py        # API routes
└── templates/     # HTML templates
```

### Frontend

```
static/
├── js/
│   └── streams.js     # Secure Agora client implementation
├── styles/
│   └── main.css       # Responsive styling
└── assets/
    └── AgoraRTC_N-4.8.0.js
```

## 🔒 Security Implementation

### Rate Limits

| Endpoint | Limit | Purpose |
|----------|-------|---------|
| `/get_token/` | 10/min per IP | Prevent token abuse |
| `/create_member/` | 30/min per IP | Prevent spam |
| `/get_member/` | 60/min per IP | Prevent scraping |
| `/delete_member/` | 30/min per IP | Prevent abuse |

### Input Validation

**Channel Names:**
- Length: 1-64 characters
- Pattern: `^[a-zA-Z0-9_-]+$`
- XSS protection: HTML escaped

**Usernames:**
- Length: 2-100 characters
- Pattern: `^[a-zA-Z0-9_\s-]+$`
- XSS protection: HTML escaped

**UIDs:**
- Range: 0 to 4,294,967,295
- Type: Integer
- Generation: Cryptographically secure random

### Authorization

- Users can only delete their own member records
- Session validation on all operations
- UID and room ownership verification

## 🚀 Deployment

### Production Checklist

- [ ] Set `DEBUG=False`
- [ ] Configure `ALLOWED_HOSTS`
- [ ] Set strong `SECRET_KEY`
- [ ] Configure Agora credentials
- [ ] Enable HTTPS
- [ ] Set up SSL certificate
- [ ] Configure reverse proxy (nginx/Apache)
- [ ] Set up database backups
- [ ] Configure log monitoring
- [ ] Test all security headers

### Heroku Deployment

```bash
# Login to Heroku
heroku login

# Create app
heroku create your-app-name

# Set environment variables
heroku config:set SECRET_KEY=your-secret-key
heroku config:set AGORA_APP_ID=your-app-id
heroku config:set AGORA_APP_CERTIFICATE=your-certificate
heroku config:set ALLOWED_HOSTS=your-app-name.herokuapp.com

# Deploy
git push heroku main

# Run migrations
heroku run python manage.py migrate

# Create superuser
heroku run python manage.py createsuperuser
```

### Docker Deployment

```dockerfile
# Dockerfile example
FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["gunicorn", "mychat.wsgi:application", "--bind", "0.0.0.0:8000"]
```

## 🧪 Testing

```bash
# Run tests
python manage.py test

# Check security
python manage.py check --deploy

# Test CSRF protection
# Try POST without CSRF token - should get 403

# Test rate limiting
# Make 11 token requests in 1 minute - should get 429
```

## 📊 Monitoring

### Log Files

- **Security logs**: `logs/security.log`
- **Application logs**: Console output

### Log Monitoring

```bash
# Watch security logs
tail -f logs/security.log

# Search for errors
grep -i "error" logs/security.log

# Check unauthorized attempts
grep -i "unauthorized" logs/security.log
```

## 🐛 Troubleshooting

### Common Issues

**"CSRF verification failed"**
- Ensure cookies are enabled
- Check CSRF_COOKIE_SECURE setting matches HTTPS usage
- Verify X-CSRFToken header in POST requests

**"Rate limit exceeded"**
- Wait for rate limit window to expire
- Check if legitimate traffic is being blocked
- Adjust rate limits in views.py if needed

**"Failed to generate token"**
- Verify AGORA_APP_ID and AGORA_APP_CERTIFICATE in .env
- Check Agora project status
- Review security.log for errors

**"Camera/Microphone not working"**
- Grant browser permissions
- Use HTTPS (required for getUserMedia)
- Check browser compatibility

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

**Security contributions welcome!**

## 📝 License

[Your License Here]

## 🙏 Acknowledgments

- [divanov11/mychat](https://github.com/divanov11/mychat), the tutorial project this repository started from
- Django framework
- Agora WebRTC SDK
- OWASP Security Guidelines

## 📞 Support

For security issues, see [SECURITY.md](SECURITY.md)

For general support, create an issue on GitHub.

---

**Built with ❤️ and 🛡️ Security**
