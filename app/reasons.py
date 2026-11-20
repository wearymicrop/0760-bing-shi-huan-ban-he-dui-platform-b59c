MESSAGES = {
    'BAD_SOURCE': '源文件应为完整的时段记录 JSON', 'EMPTY_SOURCE': '源文件无记录',
    'SOURCE_LIMIT': '源文件超出容量限制', 'NO_SAMPLE': '找不到场次样例',
    'RUN_MISSING': '找不到值班批次', 'BAD_ROW': '字段类型或值无效',
    'BAD_TIME': '时间缺少有效偏移或精确到秒', 'BAD_INTERVAL': '时段倒置或为空',
    'DUP_WINDOW': '营业日历重复', 'WINDOW_OVERLAP': '营业类别重叠',
    'DUP_PASS': '权益版本重复', 'PASS_OVERLAP': '同一权益有多个有效版本',
    'NO_PASS': '场次没有适用权益', 'NOT_OPEN': '该段缺少营业凭证',
    'DUP_EVENT': '订单事件重复', 'REV_CONFLICT': '同一修订号内容冲突',
    'STALE_EVENT': '修订号已被后续事件替代', 'AFTER_CLOSE': '事件晚于结账截点',
    'BAD_CHAIN': '没有完整的新订与改单链', 'COLLISION': '同场冰面时段重叠',
    'CANCELED': '该预订已撤销', 'LIMIT': '权益分钟额用尽',
    'LATE_CONFLICT': '晚到事件与本班版本冲突', 'LATE_STALE': '晚到事件不晚于本班版本',
    'AFTER_CANCEL': '撤销后仍有后续事件',
    'UNBALANCED': '分钟或权益未对平', 'INCOMPLETE_SOURCE': '源批次不完整',
    'DEFERRED': '仅见下班后事件', 'SHEET_PENDING': '该批次未含复核单，请重跑同批',
}


def evidence(code, line, key='', **data):
    return {'line': line, 'key': key, 'code': code, 'detail': MESSAGES[code], **data}
