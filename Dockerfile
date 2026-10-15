FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -e .
COPY . .
EXPOSE 8501
CMD ["sh", "-c", "python -m capex.pipeline all && streamlit run app/streamlit_app.py --server.address=0.0.0.0 --server.port=8501"]
