"""Explicit Boolean meanings and per-warning scope for Olist reports.
Review flags are heuristic checks, not a full-text factuality certificate.
"""
import json
import re

MARKER = 'EVIDENCE_JSON\n'


def strengthen_olist_instruction(instruction):
    prefix, separator, payload = instruction.partition(MARKER)
    if not separator:
        raise ValueError('Missing structured report evidence')
    evidence = json.loads(payload)
    values = {'source_unchanged':evidence['cleaning']['source_unchanged'],
              'gate_passed':evidence['cleaning']['gate_passed'],
              'future_rows_used':evidence['forecast']['future_rows_used']}
    if any(type(v) is not bool for v in values.values()):
        raise ValueError('Boolean facts must be actual JSON booleans')
    unchanged,gate,future = values.values()
    meanings = [
        f"source_unchanged={str(unchanged).lower()}：源文件{'保持不变' if unchanged else '发生改变'}。若使用‘源文件是否变更’行名，答案必须为‘{'否' if unchanged else '是'}’。",
        f"gate_passed={str(gate).lower()}：门禁{'通过' if gate else '未通过'}。",
        f"future_rows_used={str(future).lower()}：{'使用了' if future else '未使用'}未来记录。"]
    contract = ('\n【必须遵守的字段语义与警告范围】\n'
        +'\n'.join(meanings)
        +'\n报告优先使用原始字段名和值（source_unchanged、gate_passed、future_rows_used），不要自行把肯定命题改写为相反含义的中文问句。'
        '\n逐条保留 warnings 中各自的 action。只有 action 明确包含 not used to filter forecast records 的时间字段，才能说该字段未用于筛选预测记录。'
        'unknown_category 只转述保留原始类目并添加派生标记；orders_without_items 只转述保留订单且不虚构明细需求。'
        '不得把某条 action 扩大为所有警告对应字段都未用于筛选，也不得省略实际警告。'
        '\n不得把 source_unchanged=true 解释为“源文件已改变/是否变更：是”。没有提供独立验证或人工审批时，不声称已做。\n')
    return prefix+contract+MARKER+payload


def review_olist_report(text,evidence):
    expected = {'source_unchanged':evidence['cleaning']['source_unchanged'],
                'gate_passed':evidence['cleaning']['gate_passed'],
                'future_rows_used':evidence['forecast']['future_rows_used']}
    flags=[]
    def flag(kind,line):
        item=dict(type=kind,line=line.strip())
        if item not in flags:flags.append(item)
    for line in text.splitlines():
        if line.strip().startswith('|'):
            cells=[re.sub(r'[`*]','',c).strip() for c in line.strip().strip('|').split('|')]
            if len(cells)==2:
                key,value=cells
                low=value.lower()
                for name,wanted in expected.items():
                    if key==name:
                        found=re.match(r'(true|false)\b',low)
                        if found and (found[1]=='true') != wanted:
                            flag('raw_boolean_contradiction',line)
                # Interpret the Chinese question's polarity independently of raw text in brackets.
                if re.search(r'(?:源文件|原始文件)',key):
                    if re.search(r'保持不变|未改变|未变更|是否不变',key):
                        wanted=expected['source_unchanged']
                    elif re.search(r'是否.*(?:变更|改变|修改|改动)|有无.*(?:变更|改变|修改)',key):
                        wanted=not expected['source_unchanged']
                    else:
                        wanted=None
                    answer=re.match(r'(是|否)',value)
                    if wanted is not None and answer and (answer[1]=='是') != wanted:
                        flag('chinese_boolean_polarity_contradiction',line)
        # The same positive/negative question may occur in prose, not just tables.
        plain=re.sub(r'[`*]','',line)
        for match in re.finditer(r'((?:源文件|原始文件)[^：:\n|]{0,24})[：:]\s*(是|否)(?=[\s（(，。；;]|$)',plain):
            subject,answer=match.groups()
            if re.search(r'保持不变|未改变|未变更|是否不变',subject):
                wanted=expected['source_unchanged']
            elif re.search(r'是否.*(?:变更|改变|修改|改动)|有无.*(?:变更|改变|修改)',subject):
                wanted=not expected['source_unchanged']
            else:
                wanted=None
            if wanted is not None and (answer=='是') != wanted:
                flag('prose_boolean_polarity_contradiction',line)
        # Examine clause subjects instead of matching arbitrary distant words.
        # Explicitly scoped timestamp fields are supported by their own actions.
        for clause in re.split(r'[。；;!?！？]',plain):
            broad=re.search(r'(?:所有|全部|各类|以下|上述|这些).{0,8}(?:警告|warnings|字段)|对应字段|各字段',clause)
            action=re.search(r'(?:均|都|全部|一律).*(?:未|不).*(?:过滤|筛选)',clause)
            if broad and action:
                flag('warning_action_scope_needs_review',line)
            if (evidence.get('sql',{}).get('independent_reference_match') is True
                and re.search(r'(?:未提供|没有|缺少).{0,8}独立(?:验证|校验|核验)(?:记录|结果)?',clause)
                and not re.search(r'模型|预测|详细|明细',clause)):
                flag('independent_verification_scope_needs_review',line)
        if expected['source_unchanged'] and re.search(r'(?:源文件|原始文件)(?:已|已经|发生了|被)(?:变更|改变|修改|改动)',line):
            flag('source_change_claim_contradiction',line)
    return dict(boolean_and_scope_review_flags=flags,
                heuristic_checks_passed=not flags,
                full_text_review_required=True,
                verified_full_text_factual_accuracy=None)
