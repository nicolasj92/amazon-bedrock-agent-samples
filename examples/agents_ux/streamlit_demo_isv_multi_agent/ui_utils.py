import boto3
import streamlit as st
import datetime
import json
import math
import os
from collections import defaultdict
from src.utils.bedrock_agent import Task
from streamlit_flow import streamlit_flow
from streamlit_flow.elements import StreamlitFlowNode, StreamlitFlowEdge
from streamlit_flow.state import StreamlitFlowState
from streamlit_flow.layouts import TreeLayout

supervisor_agent = os.environ.get("SUPERVISOR_AGENT", "energy-agent-101eee65")

# Load agent mapping from environment variables
agent_id_to_name_lookup = {}

# Check for AGENT_MAPPING environment variable (JSON format)
agent_mapping_json = os.environ.get("AGENT_MAPPING", "")
if agent_mapping_json:
    try:
        agent_id_to_name_lookup = json.loads(agent_mapping_json)
    except json.JSONDecodeError:
        st.error(f"Error parsing AGENT_MAPPING environment variable: {agent_mapping_json}")
        st.stop()

def make_full_prompt(tasks, additional_instructions, processing_type="sequential"):
    """Build a full prompt from tasks and instructions."""
    prompt = ''
    if processing_type == 'sequential':
        prompt += """
Please perform the following tasks sequentially. Be sure you do not 
perform any of the tasks in parallel. If a task will require information produced from a prior task, 
be sure to include the full text details as comprehensive input to the task.\n\n"""
    elif processing_type == "allow_parallel":
        prompt += """
Please perform as many of the following tasks in parallel where possible.
When a dependency between tasks is clear, execute those tasks in sequential order. 
If a task will require information produced from a prior task,
be sure to include the comprehensive text details as input to the task.\n\n"""

    for task_num, task in enumerate(tasks, 1):
        prompt += f"Task {task_num}. {task}\n"

    prompt += "\nBefore returning the final answer, review whether you have achieved the expected output for each task."

    if additional_instructions:
        prompt += f"\n{additional_instructions}"

    return prompt

def process_routing_trace(event, step, _sub_agent_name, log_md, _time_before_routing=None,):
    """Process routing classifier trace events."""
   
    _route = event['trace']['trace']['routingClassifierTrace']
    
    if 'modelInvocationInput' in _route:
        log_md("**Choosing a collaborator for this request...**")
        #print("Processing modelInvocationInput")
        return datetime.datetime.now(), step, _sub_agent_name, None, None
        
    if 'modelInvocationOutput' in _route and _time_before_routing:
        #print("Processing modelInvocationOutput")
        _llm_usage = _route['modelInvocationOutput']['metadata']['usage']
        inputTokens = _llm_usage['inputTokens']
        outputTokens = _llm_usage['outputTokens']
        
        _route_duration = datetime.datetime.now() - _time_before_routing

        _raw_resp_str = _route['modelInvocationOutput']['rawResponse']['content']
        _raw_resp = json.loads(_raw_resp_str)
        _classification = _raw_resp['content'][0]['text'].replace('<a>', '').replace('</a>', '')

        if _classification == "undecidable":
            text = f"No matching collaborator. Revert to 'SUPERVISOR' mode for this request."
        elif _classification in (_sub_agent_name, 'keep_previous_agent'):
            step = math.floor(step + 1)
            text = f"Continue conversation with previous collaborator"
        else:
            _sub_agent_name = _classification
            step = math.floor(step + 1)
            text = f"Use collaborator: '{_sub_agent_name}'"

        #time_text = f"Intent classifier took {_route_duration.total_seconds():,.1f}s"
        log_md(text)
        #log_md(time_text)
        
        return step, _sub_agent_name, inputTokens, outputTokens

def process_orchestration_trace(event, agentClient, step, log_md):
    """Process orchestration trace events."""
    _orch = event['trace']['trace']['orchestrationTrace']
    inputTokens = 0
    outputTokens = 0
    
    if "invocationInput" in _orch:
        _input = _orch['invocationInput']
        
        if 'knowledgeBaseLookupInput' in _input:
            log_md("**Using knowledge base**")
            log_md("knowledge base id: " + _input["knowledgeBaseLookupInput"]["knowledgeBaseId"])
            log_md("query: " + _input["knowledgeBaseLookupInput"]["text"].replace('$', '\\$'))
                
        if "actionGroupInvocationInput" in _input:
            function = _input["actionGroupInvocationInput"]["function"]
            edge = agent_id_to_name_lookup[event["trace"]["agentId"]] + "-" + function
            if edge not in st.session_state["active_edges"]:
                st.session_state["active_edges"].append(edge)
            if function not in st.session_state["active_nodes"]:
                st.session_state["active_nodes"].append(function)
            log_md(f"**Invoking Tool - {function}**")
            log_md("function : " + function)
            log_md("type: " + _input["actionGroupInvocationInput"]["executionType"])
            if 'parameters' in _input["actionGroupInvocationInput"]:
                log_md("*Parameters*")
                params = _input["actionGroupInvocationInput"]["parameters"]
                for p in params:
                    log_md(f"- {p['name']}: {p['value']}")

        if 'codeInterpreterInvocationInput' in _input:
            log_md("**Code interpreter tool usage**")
            gen_code = _input['codeInterpreterInvocationInput']['code']
            log_md("```python\n" + gen_code + "\n```")
                    
    if "modelInvocationOutput" in _orch:
        if "usage" in _orch["modelInvocationOutput"]["metadata"]:
            inputTokens = _orch["modelInvocationOutput"]["metadata"]["usage"]["inputTokens"]
            outputTokens = _orch["modelInvocationOutput"]["metadata"]["usage"]["outputTokens"]
                    
    if "rationale" in _orch:
        if "agentId" in event["trace"]:
            agentData = agentClient.get_agent(agentId=event["trace"]["agentId"])
            agentName = agentData["agent"]["agentName"]
            if agentName != supervisor_agent:
                edge = supervisor_agent + "-" + agentName
                if edge not in st.session_state["active_edges"]:
                    st.session_state["active_edges"].append(edge)
                if supervisor_agent not in st.session_state["active_nodes"]:
                    st.session_state["active_nodes"].append(supervisor_agent)
                if agentName not in st.session_state["active_nodes"]:
                    st.session_state["active_nodes"].append(agentName)
            chain = event["trace"]["callerChain"]
            
            if len(chain) <= 1:
                step = math.floor(step + 1)
                log_md(f"#### Step  :blue[{round(step,2)}]")
            else:
                step = step + 0.1
                log_md(f"###### Step {round(step,2)} Sub-Agent  :red[{agentName}]")
            
            log_md(_orch["rationale"]["text"].replace('$', '\\$'))

    if "observation" in _orch:
        _obs = _orch['observation']
        
        if 'knowledgeBaseLookupOutput' in _obs:
            log_md("**Knowledge Base Response**")
            _refs = _obs['knowledgeBaseLookupOutput']['retrievedReferences']
            _ref_count = len(_refs)
            log_md(f"{_ref_count} references")
            for i, _ref in enumerate(_refs, 1):
                log_md(f"  ({i}) {_ref['content']['text'][0:200]}...")

        if 'actionGroupInvocationOutput' in _obs:
            log_md("**Tool Response**")
            log_md(_obs['actionGroupInvocationOutput']['text'].replace('$', '\\$'))

        if 'codeInterpreterInvocationOutput' in _obs:
            log_md("**Code interpreter output**")
            if 'executionOutput' in _obs['codeInterpreterInvocationOutput']:
                raw_output = _obs['codeInterpreterInvocationOutput']['executionOutput']
                log_md("```\n" + raw_output + "\n```")

            if 'executionError' in _obs['codeInterpreterInvocationOutput']:
                error_text = _obs['codeInterpreterInvocationOutput']['executionError']
                log_md(f"Code interpretation error: {error_text}")

            if 'files' in _obs['codeInterpreterInvocationOutput']:
                files_generated = _obs['codeInterpreterInvocationOutput']['files']
                log_md(f"Code interpretation files generated:\n{files_generated}")

        if 'finalResponse' in _obs:
            log_md("**Agent Response**")
            log_md(_obs['finalResponse']['text'].replace('$', '\\$'))
            
    return step, inputTokens, outputTokens

def invoke_agent(input_text, session_id, task_yaml_content, log_md):
    """Main agent invocation and response processing."""
    client = boto3.client('bedrock-agent-runtime')
    agentClient = boto3.client('bedrock-agent')

    st.session_state["active_edges"] = []
    
    # Process tasks if any
    _tasks = []
    _bot_config = st.session_state['bot_config']
    for _task_name in task_yaml_content.keys():
        _curr_task = Task(_task_name, task_yaml_content, _bot_config['inputs'])
        _tasks.append(_curr_task)
        
    if len(_tasks) > 0:
        additional_instructions = _bot_config.get('additional_instructions')
        messagesStr = make_full_prompt(_tasks, additional_instructions)
    else:
        messagesStr = input_text

    # Invoke agent
    try:
        if 'sessionAttributes' in _bot_config['session_attributes']:
            session_state = {
                "sessionAttributes": {**st.session_state["login_info"], **_bot_config['session_attributes']['sessionAttributes']}
            }
        else:
            session_state = {
                "sessionAttributes": st.session_state["login_info"]
            }

        # TODO @njourdan: add cleaner way of determining the tenant id
        del session_state["sessionAttributes"]["groups"]
        session_state["sessionAttributes"]["tenant_id"] = st.session_state["login_info"]["tenant_id"].split("-")[-1] 
        print(session_state["sessionAttributes"]["tenant_id"] )

        if 'promptSessionAttributes' in _bot_config['session_attributes']:
            session_state['promptSessionAttributes'] = _bot_config['session_attributes']['promptSessionAttributes']

        response = client.invoke_agent(
            agentId=_bot_config['agent_id'],
            agentAliasId=_bot_config['agent_alias_id'],
            sessionId=session_id,
            sessionState=session_state,
            inputText=messagesStr,
            enableTrace=True
        )

    except Exception as e:
        print(f"Error invoking agent: {e}")
        raise e

    # Process response
    step = 0.0
    _sub_agent_name = " "
    _time_before_routing = None
    inputTokens = 0
    outputTokens = 0
    _total_llm_calls = 0
    
    with st.spinner(""):
        for event in response.get("completion"):
            if "chunk" in event:
                yield event["chunk"]["bytes"].decode("utf-8").replace('$', '\\$')
                
            if "trace" in event:
                if 'routingClassifierTrace' in event['trace']['trace']:
                    result = process_routing_trace(event, step, _sub_agent_name, log_md, _time_before_routing)
                    if result:
                        if len(result) == 5:  # Initial invocation
                            _time_before_routing, step, _sub_agent_name, in_tokens, out_tokens = result
                            if in_tokens and out_tokens:
                                inputTokens += in_tokens
                                outputTokens += out_tokens
                                _total_llm_calls += 1
                        else:  # Subsequent invocation
                            step, _sub_agent_name, in_tokens, out_tokens = result
                            if in_tokens and out_tokens:
                                inputTokens += in_tokens
                                outputTokens += out_tokens
                                _total_llm_calls += 1

                        
                if "orchestrationTrace" in event["trace"]["trace"]:
                    result = process_orchestration_trace(event, agentClient, step, log_md)
                    if result:
                        step, in_tokens, out_tokens = result
                        if in_tokens and out_tokens:
                            inputTokens += in_tokens
                            outputTokens += out_tokens
                            _total_llm_calls += 1

        # Display token usage at the end
        log_md("**Usage Summary**")
        log_md("Total Input Tokens : **" + str(inputTokens) + "**")
        log_md("Total Output Tokens : **" + str(outputTokens) + "**")
        log_md("Total LLM Calls : **" + str(_total_llm_calls) + "**")