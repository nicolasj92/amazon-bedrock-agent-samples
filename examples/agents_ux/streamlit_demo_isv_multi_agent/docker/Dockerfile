FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY examples/agents_ux/streamlit_demo_isv_multi_agent/requirements.txt ./requirements.txt
COPY src/requirements.txt ./root-requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -r root-requirements.txt

# Copy the application code
COPY examples/agents_ux/streamlit_demo_isv_multi_agent/*.py ./
COPY src/ ./src/

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV REDIRECT_URI=https://d197r82edw46y9.cloudfront.net

# Agent mapping as JSON string
# ENV AGENT_MAPPING='{"T9JSE1S23X":"forecast-101eee65","JDDIIBZOYH":"solar-p-101eee65","Q2MJBPMIIH":"peak-agent-101eee65","CG8AN5GY0T":"energy-agent-101eee65"}'
ENV AGENT_MAPPING='{"Q3ODUXSFQS":"forecast-101eee65","R54NRTO6GP":"solar-p-101eee65","IIWWBNQS4V":"peak-agent-101eee65","6TCH16VG4J":"energy-agent-101eee65"}'
ENV SUPERVISOR_AGENT=energy-agent-101eee65

# Expose the port Streamlit runs on
EXPOSE 8501

# Command to run the application
CMD streamlit run demo_ui.py



# 