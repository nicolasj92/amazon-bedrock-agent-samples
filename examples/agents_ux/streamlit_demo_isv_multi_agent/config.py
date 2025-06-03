import datetime

# Bot configurations
bot_configs = [
    {
        "bot_name": "Forecasting Agent",
        "agent_name": "forecast-101eee65",
    }, 
    {
        "bot_name": "Solar Panel Agent",
        "agent_name": "solar-p-101eee65",
    },        
    {
        "bot_name": "Peak Energy Consumption Agent",
        "agent_name": "peak-agent-101eee65",
    },
    {
        "bot_name": "EnergyERP Assistant",
        "agent_name": "energy-agent-101eee65",
        "start_prompt": "I'm your energy assistant. How can I help today?",
        "session_attributes": {
            "sessionAttributes": {
                "todays_date": datetime.datetime.now().strftime("%Y-%m-%d")
            },
            "promptSessionAttributes": {
                "todays_date": datetime.datetime.now().strftime("%Y-%m-%d")
            }
        }
    }
]
