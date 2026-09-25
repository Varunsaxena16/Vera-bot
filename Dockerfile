FROM python:3.11-slim
WORKDIR /app
COPY *.py ./
ENV PORT=7860 PYTHONUNBUFFERED=1
EXPOSE 7860
CMD ["python", "bot.py"]
