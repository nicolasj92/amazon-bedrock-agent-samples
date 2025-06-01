import streamlit as st
import os
import uuid
import yaml
import sys
import boto3
import json
from botocore.exceptions import ClientError
from pathlib import Path
from streamlit_flow import streamlit_flow
from streamlit_flow.elements import StreamlitFlowNode, StreamlitFlowEdge
from streamlit_flow.state import StreamlitFlowState
from streamlit_flow.layouts import TreeLayout
from streamlit_cognito_auth import CognitoAuthenticator

sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from src.utils.bedrock_agent import agents_helper
from config import bot_configs
from ui_utils import invoke_agent

def get_secret():
    secret_name = "cognito_streamlit_auth"
    region_name = "us-west-2"

    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=region_name
    )

    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
    except ClientError as e:
        # For a list of exceptions thrown, see
        # https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
        raise e

    secret = json.loads(get_secret_value_response['SecretString'])
    return secret

def initialize_session():
    """Initialize session state and bot configuration."""
    if 'count' not in st.session_state:
        st.session_state['count'] = 1

        # Refresh agent IDs and aliases
        for idx, config in enumerate(bot_configs):
            try:
                agent_id = agents_helper.get_agent_id_by_name(config['agent_name'])
                agent_alias_id = agents_helper.get_agent_latest_alias_id(agent_id)
                bot_configs[idx]['agent_id'] = agent_id
                bot_configs[idx]['agent_alias_id'] = agent_alias_id
            except Exception as e:
                print(f"Could not find agent named:{config['agent_name']}, skipping...")
                continue

        # Get bot configuration
        bot_name = os.environ.get('BOT_NAME', 'Energy Assistant')
        bot_config = next((config for config in bot_configs if config['bot_name'] == bot_name), None)


        
        if bot_config:
            st.session_state['bot_config'] = bot_config
            
            # Load tasks if any
            task_yaml_content = {}
            if 'tasks' in bot_config:
                with open(bot_config['tasks'], 'r') as file:
                    task_yaml_content = yaml.safe_load(file)
            st.session_state['task_yaml_content'] = task_yaml_content

            # Initialize session ID and message history
            st.session_state['session_id'] = str(uuid.uuid4())
            st.session_state.messages = []

def login_flow():
    secret = get_secret()
    authenticator = CognitoAuthenticator(
        pool_id=secret["cognito_pool_id"],
        app_client_id=secret["cognito_app_client_id"],
        app_client_secret=secret["cognito_app_client_secret"],
        use_cookies=False
    )

    is_logged_in = authenticator.login()
    if not is_logged_in:
        st.stop()


    def logout():
        print("Logout in example")
        authenticator.logout()


    # Get user details from Cognito
    username = authenticator.get_username()
    
    # Create Cognito Identity Provider client
    cognito_idp = boto3.client('cognito-idp', region_name=secret.get("region_name", "us-west-2"))
    
    try:
        # Get user details
        user_response = cognito_idp.admin_get_user(
            UserPoolId=secret["cognito_pool_id"],
            Username=username
        )
        
        # Extract user attributes
        user_attributes = {attr['Name']: attr['Value'] for attr in user_response.get('UserAttributes', [])}
        first_name = user_attributes.get('given_name', '')
        last_name = user_attributes.get('family_name', '')
        
        # Get user groups
        groups_response = cognito_idp.admin_list_groups_for_user(
            Username=username,
            UserPoolId=secret["cognito_pool_id"]
        )
        groups = [group['GroupName'] for group in groups_response.get('Groups', [])]

        st.session_state["login_info"] = {
            "tenant_id": groups[0],
            "user_id": username,
            "first_name": first_name,
            "last_name": last_name
        }
        
        # Map groups to icons (consistent mapping using hash)
        # List of icons to choose from
        icons = ["🔑", "🛡️", "👤", "🔧", "📊"]
        
        # Function to consistently map group name to icon
        def get_icon_for_group(group_name):
            # Simple hash function to get a consistent index
            hash_value = sum(ord(c) for c in group_name) % len(icons)
            return icons[hash_value]
        
        # Display user information in sidebar
        with st.sidebar:
            # Add logo/header
            st.markdown("""
            <div style="text-align: center; margin-bottom: 20px;">
                <h3>⚡ ISV Energy Portal</h3>
                <hr>
            </div>
            """, unsafe_allow_html=True)
            
            # User profile section with card-like styling (more compact)
            st.markdown(f"""
            <div style="padding: 10px; border-radius: 8px; background-color: #f0f2f6; margin-bottom: 15px;">
                <div style="text-align: center;">
                    <div style="font-weight: bold; font-size: 18px;">{first_name} {last_name}</div>
                    <div style="color: #666; font-size: 14px;">{username}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            # Groups section with better styling
            if groups:
                st.markdown("""
                <div style="margin-bottom: 10px; font-weight: bold;">
                    🔑 Tenant Groups
                </div>
                """, unsafe_allow_html=True)
                
                for group in groups:
                    icon = get_icon_for_group(group)
                    st.markdown(f"""
                    <div style="padding: 8px; margin-bottom: 8px; border-radius: 5px; background-color: #e6f3ff; display: flex; align-items: center;">
                        <span style="font-size: 18px; margin-right: 10px;">{icon}</span>
                        <span>{group}</span>
                    </div>
                    """, unsafe_allow_html=True)
            
            # Add some space
            st.markdown("<br>", unsafe_allow_html=True)
            
            # Styled logout button (using standard button with some space above)
            st.button("🚪 Logout", "logout_btn", on_click=logout)          

    except Exception as e:
        print(f"Error retrieving user details: {e}")
        with st.sidebar:
            st.text(f"Welcome,\n{username}")
            st.button("Logout", "logout_btn", on_click=logout)

def main():
    """Main application flow."""

    st.set_page_config(
        page_title="Multi-Agent Energy Assistant",  # This will be your tab title
        page_icon="⚡",  # This can be an emoji or path to an image file
        layout="wide",
    )
    login_flow()
    initialize_session()

    # Display chat interface
    st.title(st.session_state['bot_config']['bot_name'])

    col1, col2 = st.columns([1, 1])

    with col1: 
        with st.container(border=False, height=1000):
            # Show welcome message if no messages yet and no user input
            if len(st.session_state.messages) == 0 and ('user_input' not in st.session_state or st.session_state["user_input"] is None):
                st.markdown("""
                            <div style="padding: 20px; border-radius: 10px; background-color: #f0f2f6; margin-bottom: 20px;">
                                <h3>👋 Welcome to the Energy Assistant!</h3>
                                <p>I can help you with various energy-related queries. Here are some examples you can try:</p>
                                <ul>
                                    <li>📊 <b>"Show me energy consumption trends for the last quarter"</b></li>
                                    <li>💡 <b>"What are some ways to reduce my energy costs?"</b></li>
                                    <li>🔌 <b>"Compare solar and wind energy efficiency"</b></li>
                                    <li>🏭 <b>"Explain carbon credits and how they work"</b></li>
                                </ul>
                                <p>Type your question below to get started!</p>
                                <div style="text-align: right; font-size: 12px; color: #666; margin-top: 10px;">Powered by Amazon Bedrock</div>
                            </div>
                """, unsafe_allow_html=True)

            # Show message history
            for idx, message in enumerate(st.session_state.messages):
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])

                if message.get("trace_md"):
                    with st.status("Details", expanded=False,):
                        st.markdown(message["trace_md"])

            # Handle user input
            if ('user_input' not in st.session_state or st.session_state["user_input"] is None):
                next_prompt = "How can I help you today?"
                user_query = st.chat_input(placeholder=next_prompt, key="user_input")
                st.session_state['bot_config']['start_prompt'] = " "
            elif st.session_state.count > 1:
                user_query = st.session_state['user_input']
                
                if user_query:
                    # Display user message
                    st.session_state.messages.append({"role": "user", "content": user_query})
                    with st.chat_message("user"):
                        st.markdown(user_query)

                    status_box = st.status("Processing...", expanded=True)
                    trace_log = []

                    def trace_writer(md):
                        trace_log.append(md)
                        status_box.markdown(md)

                    # Get and display assistant response
                    response = ""
                    with st.chat_message("assistant"):
                        try:
                            response = st.write_stream(invoke_agent(
                                user_query, 
                                st.session_state['session_id'], 
                                st.session_state['task_yaml_content'],
                                trace_writer
                            ))
                        except Exception as e:
                            print(f"Error: {e}")  # Keep logging for debugging
                            st.error(f"An error occurred: {str(e)}")  # Show error in UI
                            response = "I encountered an error processing your request. Please try again."

                    status_box.update(state="complete")

                    # Update chat history
                    st.session_state.messages.append({"role": "assistant", "content": response, "trace_md": "\n".join(trace_log)})

                # Reset input
                user_query = st.chat_input(placeholder=" ", key="user_input")

            # Update session count
            st.session_state['count'] = st.session_state.get('count', 1) + 1

    with col2:
        nodes = [
        StreamlitFlowNode('energy-agent-101eee65', 
            (0, 0), 
            {'content': 'Supervisor'}, 
            'input', 
            'right', 
            'left', 
            draggable=False),
        StreamlitFlowNode('forecast-101eee65',
            (0, 0), 
            {'content': 'Forecasting Agent'}, 
            'default', 
            'right', 
            'left', 
            draggable=False),
        StreamlitFlowNode('get_forecasted_consumption',
            (0, 0), 
            {'content': 'get_forecasted_consumption'}, 
            'output', 
            'right', 
            'left', 
            draggable=False),     
        StreamlitFlowNode('get_historical_consumption',
            (0, 0), 
            {'content': 'get_historical_consumption'}, 
            'output', 
            'right', 
            'left', 
            draggable=False),     
        StreamlitFlowNode('get_consumption_statistics',
            (0, 0), 
            {'content': 'get_consumption_statistics'}, 
            'output', 
            'right', 
            'left', 
            draggable=False),     
        StreamlitFlowNode('update_forecasting',
            (0, 0), 
            {'content': 'update_forecasting'}, 
            'output', 
            'right', 
            'left', 
            draggable=False),  
        StreamlitFlowNode('solar-p-101eee65',
            (0, 0), 
            {'content': 'Solar Installation Agent'}, 
            'default', 
            'right', 
            'left', 
            draggable=False),     
        StreamlitFlowNode('get_ticket_status',
            (0, 0), 
            {'content': 'get_ticket_status'}, 
            'output', 
            'right', 
            'left', 
            draggable=False),     
        StreamlitFlowNode('open_ticket',
            (0, 0), 
            {'content': 'open_ticket'}, 
            'output', 
            'right', 
            'left', 
            draggable=False),     
        StreamlitFlowNode('peak-agent-101eee65',
            (0, 0), 
            {'content': 'Peak Agent Agent'}, 
            'default', 
            'right', 
            'left', 
            draggable=False),
        StreamlitFlowNode('detect_peak',
            (0, 0), 
            {'content': 'detect_peak'}, 
            'output', 
            'right', 
            'left', 
            draggable=False),     
        StreamlitFlowNode('detect_non_essential_processes',
            (0, 0), 
            {'content': 'detect_non_essential_processes'}, 
            'output', 
            'right', 
            'left', 
            draggable=False),  
        StreamlitFlowNode('redistribute_allocation',
            (0, 0), 
            {'content': 'redistribute_allocation'}, 
            'output', 
            'right', 
            'left', 
            draggable=False),  
            ]

        edges = [
            StreamlitFlowEdge('energy-agent-101eee65-forecast-101eee65', 'energy-agent-101eee65', 'forecast-101eee65',  animated=False),
            StreamlitFlowEdge('energy-agent-101eee65-solar-p-101eee65', 'energy-agent-101eee65', 'solar-p-101eee65',  animated=False),
            StreamlitFlowEdge('energy-agent-101eee65-peak-agent-101eee65', 'energy-agent-101eee65', 'peak-agent-101eee65',  animated=False),

            StreamlitFlowEdge('forecast-101eee65-get_forecasted_consumption', 'forecast-101eee65', 'get_forecasted_consumption',  animated=False),
            StreamlitFlowEdge('forecast-101eee65-get_historical_consumption', 'forecast-101eee65', 'get_historical_consumption',  animated=False),
            StreamlitFlowEdge('forecast-101eee65-get_consumption_statistics', 'forecast-101eee65', 'get_consumption_statistics',  animated=False),
            StreamlitFlowEdge('forecast-101eee65-update_forecasting', 'forecast-101eee65', 'update_forecasting',  animated=False),

            StreamlitFlowEdge('solar-p-101eee65-get_ticket_status', 'solar-p-101eee65', 'get_ticket_status',  animated=False),
            StreamlitFlowEdge('solar-p-101eee65-open_ticket', 'solar-p-101eee65', 'open_ticket',  animated=False),

            StreamlitFlowEdge('peak-agent-101eee65-detect_peak', 'peak-agent-101eee65', 'detect_peak',  animated=False),
            StreamlitFlowEdge('peak-agent-101eee65-detect_non_essential_processes', 'peak-agent-101eee65', 'detect_non_essential_processes',  animated=False),
            StreamlitFlowEdge('peak-agent-101eee65-redistribute_allocation', 'peak-agent-101eee65', 'redistribute_allocation',  animated=False),
            ]

        if "curr_state" not in st.session_state:
            st.session_state.curr_state = StreamlitFlowState(nodes, edges)
            st.session_state["active_edges"] = []
            st.session_state["last_active_edges"] = []
            st.session_state["active_nodes"] = []
            st.session_state["last_active_nodes"] = []

        if st.session_state["active_edges"] != st.session_state["last_active_edges"] or st.session_state["active_nodes"] != st.session_state["last_active_nodes"]:
            for i, edge in enumerate(edges):
                if edge.id in st.session_state["active_edges"]:
                    edges[i].animated = True
                    edges[i].style = {'strokeWidth': 5, 'stroke': '#00c04b'}
                else:
                    edges[i].animated = False
                    edges[i].style = {'strokeWidth': 1}

            for i, node in enumerate(nodes):
                if node.id not in st.session_state["active_nodes"]:
                    nodes[i].style = {}
                else:
                    nodes[i].style = {'color': 'white', 'backgroundColor': '#00c04b', 'border': '2px solid white'}

            # st.session_state.curr_state.nodes = nodes
            st.session_state.curr_state.edges = edges
            st.session_state["last_active_edges"] = st.session_state["active_edges"]
            st.session_state["last_active_nodes"] = st.session_state["active_nodes"]
            # st.rerun()

        st.session_state.curr_state = streamlit_flow('tree_layout',
            st.session_state.curr_state,
            height=1000,
            layout=TreeLayout(direction='right'),
            fit_view=True,
            show_minimap=False,
            show_controls=False,
            pan_on_drag=True,
            allow_zoom=True)

                    


if __name__ == "__main__":
    main()
