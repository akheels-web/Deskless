FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# collectstatic needs a key but no DB/secrets; dummy is fine at build time.
RUN SECRET_KEY=build DEBUG=False python manage.py collectstatic --noinput

EXPOSE 8000
# invoke via sh so a missing +x bit (e.g. Windows checkout) doesn't break startup
CMD ["sh", "entrypoint.sh"]
