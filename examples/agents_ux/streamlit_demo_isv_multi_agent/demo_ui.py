import streamlit as st
import os
import uuid
import yaml
import sys
import boto3
import json
import requests
import urllib.parse
import base64
from botocore.exceptions import ClientError
from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import dataclass
from pathlib import Path
from streamlit_flow import streamlit_flow
from streamlit_flow.elements import StreamlitFlowNode, StreamlitFlowEdge
from streamlit_flow.state import StreamlitFlowState
from streamlit_flow.layouts import TreeLayout

sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from src.utils.bedrock_agent import agents_helper
from config import bot_configs
from ui_utils import invoke_agent

def get_cognito_secrets(secret_name: str = "cognito_streamlit_auth", region_name: str = "us-west-2") -> Dict[str, str]:
    """Retrieve Cognito configuration from AWS Secrets Manager"""
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=region_name
    )

    try:
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        st.error(f"Failed to retrieve secrets: {str(e)}")
        raise e

    secret = json.loads(get_secret_value_response['SecretString'])
    return secret


@dataclass
class CognitoConfig:
    """Configuration for Cognito authentication"""
    domain: str
    client_id: str
    client_secret: Optional[str]
    redirect_uri: str
    region: str
    pool_id: str

    @classmethod
    def from_secrets_manager(cls, secret_name: str = "cognito_streamlit_auth", 
                           region_name: str = "us-west-2",
                           redirect_uri: str = None) -> 'CognitoConfig':
        """Create config from AWS Secrets Manager"""
        secrets = get_cognito_secrets(secret_name, region_name)
        
        # Use environment variable for redirect URI if available, otherwise use default
        if redirect_uri is None:
            redirect_uri = os.environ.get('REDIRECT_URI', 'http://localhost:8501')
        
        # Extract domain from pool_id if not explicitly provided
        pool_id = secrets["cognito_pool_id"]
        domain = secrets["cognito_domain"]

        return cls(
            domain=domain,
            client_id=secrets["cognito_app_client_id"],
            client_secret=secrets.get("cognito_app_client_secret"),
            redirect_uri=redirect_uri,
            region=region_name,
            pool_id=pool_id,
        )


class EnhancedCognitoAuth:
    """Enhanced Cognito authentication with user details and groups"""
    
    def __init__(self, config: CognitoConfig):
        self.config = config
        self._initialize_session_state()
        self._cognito_client = None
    
    def _initialize_session_state(self):
        """Initialize Streamlit session state variables"""
        if 'auth_state' not in st.session_state:
            st.session_state.auth_state = {
                'authenticated': False,
                'user_info': None,
                'tokens': None,
                'auth_time': None,
                'cognito_user_details': None,
                'user_groups': []
            }
        
        if 'login_info' not in st.session_state:
            st.session_state.login_info = {}
    
    @property
    def cognito_client(self):
        """Lazy initialization of Cognito client"""
        if self._cognito_client is None:
            self._cognito_client = boto3.client('cognito-idp', region_name=self.config.region)
        return self._cognito_client
    
    @property
    def is_authenticated(self) -> bool:
        """Check if user is currently authenticated"""
        return st.session_state.auth_state['authenticated']
    
    @property
    def user_info(self) -> Optional[Dict[str, Any]]:
        """Get current user information"""
        return st.session_state.auth_state['user_info']
    
    @property
    def username(self) -> Optional[str]:
        """Get current username"""
        user_info = self.user_info
        return user_info.get('cognito:username') if user_info else None
    
    def get_login_url(self, state: Optional[str] = None) -> str:
        """Generate the Cognito hosted UI login URL"""
        params = {
            'client_id': self.config.client_id,
            'response_type': 'code',
            'redirect_uri': self.config.redirect_uri,
        }
        
        if state:
            params['state'] = state
        
        base_url = f"https://{self.config.domain}/login"
        return f"{base_url}?{urllib.parse.urlencode(params)}"
    
    def get_logout_url(self) -> str:
        """Generate logout URL"""
        params = {
            'client_id': self.config.client_id,
            'logout_uri': self.config.redirect_uri
        }
        return f"https://{self.config.domain}/logout?{urllib.parse.urlencode(params)}"
    
    def exchange_code_for_tokens(self, auth_code: str) -> Optional[Dict[str, Any]]:
        """Exchange authorization code for access tokens"""
        token_url = f"https://{self.config.domain}/oauth2/token"
        
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        
        data = {
            'grant_type': 'authorization_code',
            'client_id': self.config.client_id,
            'code': auth_code,
            'redirect_uri': self.config.redirect_uri
        }
        
        # Add client secret if available
        if self.config.client_secret:
            auth_string = f"{self.config.client_id}:{self.config.client_secret}"
            auth_bytes = auth_string.encode('ascii')
            auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
            headers['Authorization'] = f'Basic {auth_b64}'
        
        try:
            response = requests.post(token_url, headers=headers, data=data, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            st.error(f"Token exchange failed: {str(e)}")
            return None
    
    def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        """Get user information using access token"""
        userinfo_url = f"https://{self.config.domain}/oauth2/userInfo"
        headers = {'Authorization': f'Bearer {access_token}'}
        
        try:
            response = requests.get(userinfo_url, headers=headers, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            st.error(f"Failed to get user info: {str(e)}")
            return None
    
    def get_cognito_user_details(self, username: str) -> Optional[Dict[str, Any]]:
        """Get detailed user information from Cognito User Pool"""
        try:
            user_response = self.cognito_client.admin_get_user(
                UserPoolId=self.config.pool_id,
                Username=username
            )

            # Extract user attributes
            user_attributes = {attr['Name']: attr['Value'] for attr in user_response.get('UserAttributes', [])}
            
            # Get user groups
            groups_response = self.cognito_client.admin_list_groups_for_user(
                Username=username,
                UserPoolId=self.config.pool_id
            )
            groups = [group['GroupName'] for group in groups_response.get('Groups', [])]
            
            return {
                'attributes': user_attributes,
                'groups': groups,
                'user_status': user_response.get('UserStatus'),
                'enabled': user_response.get('Enabled', True)
            }
                
        except Exception as e:
            st.error(f"Error retrieving user details: {str(e)}")
            return None
    
    def handle_callback(self, query_params: Dict[str, Any]) -> bool:
        """Handle the OAuth callback with authorization code"""
        if 'code' not in query_params or self.is_authenticated:
            return False
        
        auth_code = query_params['code']
        
        with st.spinner("Processing authentication..."):
            # Exchange code for tokens
            tokens = self.exchange_code_for_tokens(auth_code)
            if not tokens:
                return False
            
            # Get user information
            user_info = self.get_user_info(tokens['access_token'])
            if not user_info:
                return False
            
            # Get detailed Cognito user information
            username = user_info.get('username')
            cognito_details = self.get_cognito_user_details(username) if username else None
            
            # Update session state
            st.session_state.auth_state.update({
                'authenticated': True,
                'user_info': user_info,
                'tokens': tokens,
                'auth_time': datetime.now(),
                'cognito_user_details': cognito_details,
                'user_groups': cognito_details.get('groups', []) if cognito_details else []
            })

            # Set login_info for compatibility with existing code
            if cognito_details:
                attributes = cognito_details.get('attributes', {})
                groups = cognito_details.get('groups', [])
                
                st.session_state.login_info = {
                    "tenant_id": groups[0] if groups else "default",
                    "user_id": username,
                    "first_name": attributes.get('given_name', ''),
                    "last_name": attributes.get('family_name', ''),
                    "email": attributes.get('email', ''),
                    "groups": groups
                }
            
            return True
    
    def logout(self):
        """Clear authentication state and redirect to logout URL"""
        # Clear session state
        st.session_state.auth_state = {
            'authenticated': False,
            'user_info': None,
            'tokens': None,
            'auth_time': None,
            'cognito_user_details': None,
            'user_groups': []
        }
        
        # Clear login_info
        st.session_state.login_info = {}
        
        # Redirect to Cognito logout
        logout_url = self.get_logout_url()
        st.rerun()

    
    def render_sidebar_user_info(self):
        """Render user information in sidebar (matching your existing style)"""
        if not self.is_authenticated:
            return
        
        login_info = st.session_state.get('login_info', {})
        first_name = login_info.get('first_name', '')
        last_name = login_info.get('last_name', '')
        username = login_info.get('user_id', '')
        groups = login_info.get('groups', [])
        
        with st.sidebar:
            # Add logo/header
            st.markdown("""
            <div style="text-align: center; margin-bottom: 20px;">
                <h3>⚡ EnergyERP Portal</h3>
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
                
                # Map groups to icons (consistent mapping using hash)
                icons = ["⚙️", "🏭", "🔩", "⛽", "🏗️"]
                
                def get_icon_for_group(group_name):
                    hash_value = sum(ord(c) for c in group_name) % len(icons)
                    return icons[hash_value]
                
                # Get group descriptions
                group_descriptions = {}
                try:
                    for group in groups:
                        group_info = self.cognito_client.get_group(
                            GroupName=group,
                            UserPoolId=self.config.pool_id
                        )
                        group_descriptions[group] = group_info.get('Group', {}).get('Description', '')
                except Exception as e:
                    print(f"Error fetching group descriptions: {str(e)}")
                
                for group in groups:
                    icon = get_icon_for_group(group)
                    company_name = group_descriptions.get(group, '')
                    st.markdown(f"""
                    <div style="padding: 8px; margin-bottom: 8px; border-radius: 5px; background-color: #e6f3ff; display: flex; align-items: center;">
                        <span style="font-size: 18px; margin-right: 10px;">{icon}</span>
                        <div>
                            <div style="font-weight: bold;">{company_name}</div>
                            <div>Tenant ID: {group}</div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
            
            # Add some space
            st.markdown("<br>", unsafe_allow_html=True)
            
            # Styled logout button
            if st.button("🚪 Logout", key="logout_btn"):
                self.logout()

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
        bot_name = os.environ.get('BOT_NAME', 'EnergyERP Assistant')
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



def main():
    """Main application flow."""

    st.set_page_config(
        page_title="Multi-Agent Energy Assistant",  # This will be your tab title
        page_icon="⚡",  # This can be an emoji or path to an image file
        layout="wide",
    )

     # Initialize authentication
    try:
        config = CognitoConfig.from_secrets_manager(
            secret_name="cognito_streamlit_auth",
            region_name="us-west-2"
            # No redirect_uri specified - will be determined from environment variable
        )
    except Exception as e:
        st.error(f"Failed to load configuration: {str(e)}")
        st.stop()
    
    auth = EnhancedCognitoAuth(config)
    
    # Handle OAuth callback
    query_params = dict(st.query_params)
    if auth.handle_callback(query_params):
        st.success("Authentication successful! 🎉")
        st.query_params.clear()
        st.rerun()
    
    # Check authentication
    if not auth.is_authenticated:
        login_url = auth.get_login_url()
        # Use JavaScript to automatically redirect to the login page
        st.markdown(
            f"""
            <meta http-equiv="refresh" content="0;url={login_url}">
            <script>window.location.href = "{login_url}";</script>
            """,
            unsafe_allow_html=True
        )
        st.stop()
    
    # Render sidebar user info
    auth.render_sidebar_user_info()
    
    # Initialize session for main app
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
                        <h3>🏢 Welcome to EnergyERP Multi-Agent Assistant!</h3>
                        <p>I coordinate specialized AI agents to help optimize your facility's energy management. Try asking:</p>
                        <ul>
                            <li>📊 <b>"Can you analyze our usage over the past month and identify any unusual spikes?"</b></li>
                            <li>☀️ <b>"Can you help me understand if solar panels would be cost-effective?"</b></li>
                            <li>⚡ <b>"What's causing my peak load?"</b></li>
                            <li>🎫 <b>"Can I get all tickets that I have?"</b></li>
                        </ul>
                        <p><b>Available Agents:</b> Forecasting • Solar Panel Management • Peak Load Optimization</p>
                        <p>Type your energy management question below - I'll route it to the right specialist!</p>
                        <div style="text-align: right; font-size: 12px; color: #666; margin-top: 10px;">
                            EnergyERP Multi-Agent System • Powered by Amazon Bedrock
                        </div>
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
                        # try:
                        response = st.write_stream(invoke_agent(
                            user_query, 
                            st.session_state['session_id'], 
                            st.session_state['task_yaml_content'],
                            trace_writer
                        ))
                        # except Exception as e:
                        #     print(f"Error: {e}")  # Keep logging for debugging
                        #     st.error(f"An error occurred: {str(e)}")  # Show error in UI
                        #     response = "I encountered an error processing your request. Please try again."

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
