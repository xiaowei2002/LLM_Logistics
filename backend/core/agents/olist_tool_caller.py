"""Bounded one-hop Olist function calling. Does not generate arbitrary SQL.
Models select a tool and bound arguments; numerical history stays in code.
"""
from copy import deepcopy
import json

NAMES={'sql_history':'olist_query_history','forecast':'olist_forecast_weekly'}
SCHEMAS={
    'sql_history':dict(category_key={'type':'string'},start_week={'type':'string'},origin_week={'type':'string'}),
    'forecast':dict(category_key={'type':'string'},origin_week={'type':'string'},model={'type':'string'},horizon={'type':'integer'})}


class OlistToolCallRejected(ValueError):
    def __init__(self,reason,audit):
        super().__init__(reason)
        self.audit=deepcopy(audit)


class OlistToolCaller:
    def __init__(self,model=None,*,controlled=False):
        self.model=model
        self.controlled=controlled
        self.events=[]

    def execute(self,stage,expected_arguments,executor):
        if stage not in NAMES or set(expected_arguments)!=set(SCHEMAS[stage]):
            raise ValueError('Unknown stage or unexpected bound argument schema')
        if self.model is None:
            from core.llm import get_llm
            self.model=get_llm().model
        name=NAMES[stage]
        tool={'type':'function','function':dict(name=name,
            description='Execute the workflow-bound Olist history query.' if stage=='sql_history' else 'Forecast with the workflow-bound frozen Olist weekly model.',
            parameters=dict(type='object',properties=SCHEMAS[stage],required=list(SCHEMAS[stage]),additionalProperties=False))}
        instruction=('请调用所提供的工具一次。参数必须使用以下已验证工作流上下文的原值，不得更改日期、类别、模型或步长。'
            '不要自行生成SQL、数值、文件路径或最终报告。该接口只验证受限工具调用，不执行任意SQL。\nBOUND_ARGUMENTS\n'
            +json.dumps(expected_arguments,ensure_ascii=False))
        audit=dict(stage=stage,expected_tool=name,expected_arguments=deepcopy(expected_arguments),
            controlled_model=self.controlled,model_invocations=0,arguments_validated=False,tool_executions=0)
        self.events.append(audit)
        try:
            audit['model_invocations']=1
            try:
                response=self.model.bind_tools([tool]).invoke([{'role':'user','content':instruction}])
            except Exception as exc:
                # Provider errors may echo credentials; persist only the exception type.
                audit['model_error_type']=type(exc).__name__
                raise ValueError(f'LLM tool invocation failed ({type(exc).__name__})') from exc
            calls=getattr(response,'tool_calls',None)
            audit['assistant_text']=getattr(response,'content','')
            audit['tool_calls']=deepcopy(calls)
            if getattr(response,'invalid_tool_calls',None) or not isinstance(calls,list) or len(calls)!=1:
                raise ValueError('Exactly one valid tool call is required; no text-only or parallel fallback')
            call=calls[0]
            if call.get('name')!=name or not isinstance(call.get('id'),str) or not call['id']:
                raise ValueError('Unexpected tool name or missing tool-call ID')
            arguments=call.get('args')
            if not isinstance(arguments,dict) or set(arguments)!=set(expected_arguments):
                raise ValueError('Tool argument keys differ from bound workflow')
            if any(type(arguments[k]) is not type(v) or arguments[k]!=v for k,v in expected_arguments.items()):
                raise ValueError('Tool arguments differ from bound workflow values')
            audit['arguments_validated']=True
            audit['tool_executions']=1
            result=executor(deepcopy(arguments))
            # Raw numeric results go directly to the next graph node, not model prose.
            audit['execution_status']='success'
            return result,deepcopy(audit)
        except Exception as exc:
            audit['execution_status']='rejected_or_failed'
            audit['error']=f'{type(exc).__name__}: {exc}'
            raise OlistToolCallRejected(str(exc),audit) from exc


class ControlledToolModel:
    """Preflight-only scripted model; never evidence of real LLM accuracy."""
    def bind_tools(self,tools):
        self.name=tools[0]['function']['name']
        return self

    def invoke(self,messages):
        from langchain_core.messages import AIMessage
        args=json.loads(messages[0]['content'].split('BOUND_ARGUMENTS\n',1)[1])
        return AIMessage(content='',tool_calls=[dict(name=self.name,args=args,id='controlled_call')])
