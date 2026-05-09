/* llm-router i18n — zh/en bilingual support
 * Storage key: llm-router-lang
 * Default: browser language detection (zh* → zh, else en)
 * Usage in templates:
 *   data-i18n="key"              → replaces textContent
 *   data-i18n-placeholder="key" → replaces placeholder attribute
 *   data-i18n-title="key"       → replaces title attribute
 *   data-i18n-page / data-i18n-total → server-side page info spans
 *   data-lang="zh|en"           → show/hide blocks (docs bilingual sections)
 * Global:
 *   window.t(key)        → translate a key
 *   window.i18n.setLang(lang)
 *   window.i18n.onLangChange(fn)
 */
(function () {
  'use strict';
  var STORAGE_KEY = 'llm-router-lang';

  var ZH = {
    'nav.dashboard': '控制台', 'nav.api_keys': 'API Keys',
    'nav.logical_models': '逻辑模型', 'nav.providers': '下游 Provider',
    'nav.requests': '请求日志', 'nav.billing': '账单',
    'nav.statistics': '统计', 'nav.playground': 'Playground', 'nav.docs': '文档',
    'sidebar.subtitle': '路由、计费和运维配置面板。',
    'sidebar.collapse': '收起导航', 'sidebar.expand': '展开导航',
    'common.logout': '退出登录', 'common.save': '保存', 'common.search': '搜索',
    'common.reset': '重置', 'common.edit': '编辑', 'common.delete': '删除',
    'common.enable': '启用', 'common.disable': '禁用', 'common.copy': '复制',
    'common.all': '全部', 'common.name': '名称', 'common.status': '状态',
    'common.actions': '操作', 'common.description': '描述',
    'common.yes': '是', 'common.no': '否',
    'common.prev_page': '上一页', 'common.next_page': '下一页',
    'common.select_all': '全选', 'common.clear_all': '清空',
    'common.loading': '载入中...', 'common.network_error': '网络错误，请重试',
    'common.save_success': '保存成功', 'common.save_fail': '保存失败',
    'common.op_fail': '操作失败', 'common.delete_fail': '删除失败',
    'common.page_info': '第 {cur} / {total} 页',
    'common.page_info_count': '第 {cur} / {total} 页（共 {count} 条）',
    'login.welcome': '欢迎回来',
    'login.subtitle': '使用管理员账号进入控制台。按 Enter 也可以直接提交登录。',
    'login.username': '用户名', 'login.password': '密码', 'login.submit': '登录',
    'setup.title': '初始化超管账号',
    'setup.subtitle': '系统尚未配置任何管理员账号。请设置一个超级管理员账号以继续使用控制台。',
    'setup.db_missing': '数据库表不存在',
    'setup.db_missing_desc': '检测到数据库中尚未建表。如果数据库账号没有 DDL 权限，请将下方完整建表 SQL 提交给 DBA 执行后刷新此页面。',
    'setup.copy_sql': '复制全部 SQL',
    'setup.sql_after': '执行建表 SQL 后，请刷新页面继续初始化。',
    'setup.username': '用户名', 'setup.password': '密码',
    'setup.password_placeholder': '至少 6 位',
    'setup.confirm_password': '确认密码', 'setup.confirm_placeholder': '再次输入密码',
    'setup.submit': '完成初始化', 'setup.refresh': '刷新 — 检查表是否已创建',
    'dashboard.title': '系统概览', 'dashboard.recent_requests': '最近请求',
    'dashboard.recent_ledger': '最近账本', 'dashboard.recent_daily': '最近日报',
    'dashboard.view_all': '查看全部',
    'api_keys.title': 'API Key 管理', 'api_keys.add': '新增 API Key',
    'api_keys.new_key_notice': '新建 API Key 明文，仅展示一次：',
    'api_keys.search_name': '搜索名称...', 'api_keys.search_user': '搜索用户...',
    'api_keys.search_model': '搜索模型...',
    'api_keys.col_user': '用户', 'api_keys.col_balance': '余额',
    'api_keys.col_daily_spend': '今日消费',
    'api_keys.no_data': '没有找到相关 API Key。',
    'api_keys.btn_topup': '充值', 'api_keys.btn_reveal': '查看 Key',
    'api_keys.modal_edit_title': '编辑 API Key', 'api_keys.modal_topup_title': '充值',
    'api_keys.modal_create_title': '创建 API Key', 'api_keys.modal_reveal_title': '查看 API Key',
    'api_keys.reveal_notice': '请注意保持此 Key 的机密，不要泄露给无关人员。',
    'api_keys.btn_copy_key': '复制',
    'api_keys.lbl_daily_limit': '日限额', 'api_keys.lbl_daily_limit_hint': '留空不限制',
    'api_keys.lbl_qps': 'QPS 限制', 'api_keys.lbl_allowed_models': '允许访问的逻辑模型',
    'api_keys.lbl_req_logging': '记录请求内容', 'api_keys.lbl_res_logging': '记录响应内容',
    'api_keys.lbl_end_user_hint': '（可选，作为此 Key 的默认 x-end-user 值）',
    'api_keys.lbl_timezone': '时区',
    'api_keys.lbl_timezone_hint': '（IANA 格式，例如 Asia/Shanghai，留空则不修改）',
    'api_keys.lbl_channel': '默认渠道 (Channel)',
    'api_keys.lbl_channel_hint': '（可选，作为此 Key 的默认 x-channel 值，例如 PERSONAL）',
    'api_keys.lbl_initial_balance': '初始余额', 'api_keys.lbl_topup_amount': '充值金额',
    'api_keys.lbl_remark': '备注', 'api_keys.btn_confirm_topup': '确认充值',
    'api_keys.btn_create': '创建 API Key',
    'api_keys.log_default': '默认（跟随全局配置）', 'api_keys.log_on': '开启', 'api_keys.log_off': '关闭',
    'api_keys.toast_saved': '保存成功', 'api_keys.toast_topup_fail': '充值失败',
    'api_keys.toast_copied': '已复制到剪贴板', 'api_keys.toast_copy_fail': '复制失败',
    'api_keys.no_match_model': '无匹配模型',
    'api_keys.confirm_disable': '确认禁用 {name} 吗？',
    'api_keys.confirm_enable': '确认启用 {name} 吗？',
    'api_keys.confirm_delete': '确认删除 「{name}」 吗？\n\n该 API Key 将被软删除（保留历史账单和日志），立即失效无法继续使用。',
    'api_keys.toast_disabled': '{name} 已禁用', 'api_keys.toast_enabled': '{name} 已启用',
    'api_keys.toast_deleted': '「{name}」 已永久删除',
    'logical.title': '逻辑模型', 'logical.add': '新增逻辑模型',
    'logical.no_desc': '无描述', 'logical.no_data': '没有找到相关模型。',
    'logical.btn_routes': '路由',
    'logical.modal_edit_title': '编辑逻辑模型', 'logical.modal_create_title': '创建逻辑模型',
    'logical.modal_routes_title': '路由规则',
    'logical.lbl_model_name': '模型名称', 'logical.lbl_enable': '启用',
    'logical.btn_add_route': '新增路由',
    'logical.modal_create_route_title': '新增路由', 'logical.modal_edit_route_title': '编辑路由',
    'logical.lbl_priority': '优先级', 'logical.lbl_weight': '权重',
    'logical.lbl_fallback': '后备路由', 'logical.btn_create_route': '创建路由',
    'logical.col_priority': '优先级', 'logical.col_weight': '权重',
    'logical.col_fallback': '后备', 'logical.col_health': '健康状态',
    'logical.fallback_yes': '是', 'logical.fallback_no': '否',
    'logical.health_ok': '健康', 'logical.health_degraded': '降级',
    'logical.degraded_auth': '认证失败', 'logical.degraded_quota': '配额耗尽',
    'logical.degraded_unavailable': '不可用', 'logical.degraded_error': '异常',
    'logical.btn_recover': '立即恢复', 'logical.no_routes': '没有找到相关路由。',
    'logical.confirm_delete': '确认删除逻辑模型「{name}」吗？\n\n请确保已删除该模型下的所有路由。',
    'logical.confirm_delete_route': '确认删除这个路由吗？',
    'logical.recover_failed': '恢复失败', 'logical.request_failed': '请求失败，请重试',
    'logical.toast_deleted': '已删除',
    'providers.title': '下游 Provider', 'providers.add': '新增 Provider',
    'providers.no_data': '没有找到相关 Provider。',
    'providers.btn_copy': '复制',
    'providers.modal_edit_title': '编辑 Provider', 'providers.modal_create_title': '新增 Provider',
    'providers.lbl_upstream_model': '上游模型名',
    'providers.lbl_oai_hint': '（与 Anthropic Endpoint 至少填一个）',
    'providers.lbl_ant_hint': '（与 OpenAI Endpoint 至少填一个）',
    'providers.lbl_new_api_key': '新 API Key', 'providers.lbl_no_update': '留空表示不更新',
    'providers.lbl_input_price': '输入单价 / 1M', 'providers.lbl_output_price': '输出单价 / 1M',
    'providers.lbl_prompt_cache': '支持 Prompt Cache', 'providers.lbl_timeout': '超时秒数',
    'providers.lbl_enable': '启用', 'providers.btn_create': '创建 Provider',
    'providers.validate_endpoint': 'OpenAI Endpoint 与 Anthropic Endpoint 至少填一个',
    'providers.confirm_delete': '确认删除 Provider「{name}」吗？\n\n该 Provider 将被软删除（保留历史数据），关联路由需提前手动删除。',
    'providers.toast_deleted': '已删除',
    'requests.title': '请求日志', 'requests.back': '返回控制台',
    'requests.lbl_status': '请求状态', 'requests.lbl_time_range': '开始时间范围',
    'requests.opt_all': '全部', 'requests.opt_success': '成功',
    'requests.opt_failed': '失败 (≥400)', 'requests.opt_error': '失败 (≥500)',
    'requests.preset_1h': '近1小时', 'requests.preset_6h': '近6小时',
    'requests.preset_24h': '近24小时', 'requests.preset_7d': '近7天',
    'requests.clear': '清除', 'requests.to': '至', 'requests.col_cost': '费用',
    'requests.btn_detail': '详情', 'requests.no_data': '没有找到相关日志。',
    'requests.prev': '上一页', 'requests.next': '下一页',
    'detail.title': '请求详情', 'detail.back': '返回列表',
    'detail.status_success': '成功', 'detail.status_fail': '失败',
    'detail.section_basic': '基础信息', 'detail.section_usage': '用量与费用',
    'detail.lbl_logical_model': '逻辑模型', 'detail.lbl_provider_model': '供应商模型',
    'detail.lbl_call_type': '请求类型', 'detail.lbl_protocol': '供应商协议',
    'detail.lbl_upstream_id': '上游请求 ID', 'detail.lbl_end_user': '终端用户',
    'detail.lbl_input': '输入', 'detail.lbl_reasoning': '↳ 思考',
    'detail.lbl_cached': '↳ 已缓存', 'detail.lbl_output': '输出',
    'detail.lbl_cache_write': '缓存写入', 'detail.lbl_cost_input': '输入成本',
    'detail.lbl_cost_output': '输出成本', 'detail.lbl_cost_cache_read': '缓存读成本',
    'detail.lbl_cost_cache_write': '缓存写成本', 'detail.lbl_total_cost': '总成本',
    'detail.no_usage': '暂无用量记录', 'detail.tab_request': '请求体',
    'detail.tab_response': '响应体 / 错误',
    'detail.no_request': '未记录请求内容', 'detail.no_response': '未记录响应内容',
    'billing.title': '账单与账本', 'billing.all_keys': '全部 API Key',
    'billing.btn_filter': '筛选', 'billing.btn_reset': '重置',
    'billing.ledger_title': '账本流水', 'billing.search_remark': '搜索备注...',
    'billing.all_types': '全部类型', 'billing.topup_label': '充値 (topup)',
    'billing.charge_label': '扣费 (charge)', 'billing.adjust_label': '调整 (adjust)',
    'billing.refund_label': '退款 (refund)', 'billing.no_ledger': '没有找到相关账本。',
    'billing.col_type': '类型', 'billing.col_amount': '金额',
    'billing.col_before': '变动前', 'billing.col_after': '变动后', 'billing.col_remark': '备注',
    'billing.usage_title': '请求费用记录', 'billing.search_request': '搜索 Request ID...',
    'billing.no_usage': '没有找到相关费用记录。',
    'billing.daily_title': '每日费用日报', 'billing.search_daily': '搜索日期或 API Key...',
    'billing.no_daily': '没有找到相关日报。',
    'billing.col_requests': '请求数', 'billing.col_cost': '总费用',
    'statistics.title': '请求统计', 'statistics.lbl_date_range': '日期范围',
    'statistics.preset_today': '今天', 'statistics.preset_7d': '近7天',
    'statistics.preset_30d': '近30天', 'statistics.preset_month': '本月',
    'statistics.preset_year': '今年', 'statistics.clear': '清除', 'statistics.to': '至',
    'statistics.lbl_granularity': '统计粒度', 'statistics.lbl_group_by': '分组维度',
    'statistics.group_none': '不分组', 'statistics.btn_query': '查询',
    'statistics.card_total': '总请求数', 'statistics.card_success': '成功率',
    'statistics.card_tokens': '总 Tokens', 'statistics.card_cost': '总费用 (USD)',
    'statistics.table_title': '明细数据',
    'statistics.col_date': '日期', 'statistics.col_requests': '请求数',
    'statistics.col_success': '成功', 'statistics.col_failed': '失败',
    'statistics.col_success_rate': '成功率', 'statistics.col_cost': '费用 (USD)',
    'statistics.no_data': '暂无数据。', 'statistics.exact_match': '精确匹配...',
    'playground.lbl_config': '请求配置', 'playground.lbl_api_type': 'API 类型',
    'playground.btn_send': '发送请求', 'playground.btn_clear': '清空',
    'playground.tab_request': '请求体', 'playground.tab_response': '响应体',
    'playground.lbl_elapsed': '耗时:', 'playground.status_unsent': '未发送',
    'playground.status_sending': '发送中', 'playground.status_stream': '流式响应',
    'playground.no_payload': 'GET 请求，无请求体',
    'playground.waiting': '等待发送请求...',
    'playground.json_error': 'JSON 格式有误，请检查请求报文',
    'playground.oai_protocol': 'OpenAI 协议', 'playground.ant_protocol': 'Anthropic 协议',
    'playground.status_err': '错误',
    'docs.title': '接入文档', 'docs.copy': '复制',
    'docs.toc': '目录', 'docs.example_code': '示例代码',
    'docs.section_api': 'API 接入文档', 'docs.section_dev': '编程工具集成',
    'docs.section_ops': '运维文档',
    'docs.toc_api': 'API 接入文档', 'docs.toc_req_headers': '请求头',
    'docs.toc_oai_chat': 'OpenAI Chat Completions',
    'docs.toc_oai_models': 'OpenAI List Models',
    'docs.toc_oai_emb': 'OpenAI Embeddings',
    'docs.toc_ant_msg': 'Anthropic Messages',
    'docs.toc_ant_models': 'Anthropic List Models',
    'docs.toc_dev': '编程工具集成', 'docs.toc_claude_code': 'Claude Code',
    'docs.toc_ops': '运维文档', 'docs.toc_ops_guide': '操作指引',
    'docs.toc_routing': '路由规则',
  };

  var EN = {
    'nav.dashboard': 'Dashboard', 'nav.api_keys': 'API Keys',
    'nav.logical_models': 'Logical Models', 'nav.providers': 'Providers',
    'nav.requests': 'Request Logs', 'nav.billing': 'Billing',
    'nav.statistics': 'Statistics', 'nav.playground': 'Playground', 'nav.docs': 'Docs',
    'sidebar.subtitle': 'Routing, billing and ops panel.',
    'sidebar.collapse': 'Collapse sidebar', 'sidebar.expand': 'Expand sidebar',
    'common.logout': 'Logout', 'common.save': 'Save', 'common.search': 'Search',
    'common.reset': 'Reset', 'common.edit': 'Edit', 'common.delete': 'Delete',
    'common.enable': 'Enable', 'common.disable': 'Disable', 'common.copy': 'Copy',
    'common.all': 'All', 'common.name': 'Name', 'common.status': 'Status',
    'common.actions': 'Actions', 'common.description': 'Description',
    'common.yes': 'Yes', 'common.no': 'No',
    'common.prev_page': 'Previous', 'common.next_page': 'Next',
    'common.select_all': 'Select All', 'common.clear_all': 'Clear All',
    'common.loading': 'Loading...', 'common.network_error': 'Network error, please retry',
    'common.save_success': 'Saved', 'common.save_fail': 'Save failed',
    'common.op_fail': 'Operation failed', 'common.delete_fail': 'Delete failed',
    'common.page_info': 'Page {cur} of {total}',
    'common.page_info_count': 'Page {cur} of {total} ({count} items)',
    'login.welcome': 'Welcome Back',
    'login.subtitle': 'Sign in with your admin account. Press Enter to submit.',
    'login.username': 'Username', 'login.password': 'Password', 'login.submit': 'Sign In',
    'setup.title': 'Initialize Admin Account',
    'setup.subtitle': 'No admin account configured. Set up a super admin account to use the console.',
    'setup.db_missing': 'Database Tables Missing',
    'setup.db_missing_desc': 'Database tables not found. If the DB account lacks DDL privileges, submit the SQL below to your DBA and refresh this page.',
    'setup.copy_sql': 'Copy SQL',
    'setup.sql_after': 'After running the SQL, refresh this page to continue.',
    'setup.username': 'Username', 'setup.password': 'Password',
    'setup.password_placeholder': 'At least 6 characters',
    'setup.confirm_password': 'Confirm Password', 'setup.confirm_placeholder': 'Re-enter password',
    'setup.submit': 'Complete Setup', 'setup.refresh': 'Refresh — Check if tables exist',
    'dashboard.title': 'System Overview', 'dashboard.recent_requests': 'Recent Requests',
    'dashboard.recent_ledger': 'Recent Ledger', 'dashboard.recent_daily': 'Recent Daily Summary',
    'dashboard.view_all': 'View All',
    'api_keys.title': 'API Key Management', 'api_keys.add': 'New API Key',
    'api_keys.new_key_notice': 'New API Key (shown once only):',
    'api_keys.search_name': 'Search name...', 'api_keys.search_user': 'Search user...',
    'api_keys.search_model': 'Search model...',
    'api_keys.col_user': 'User', 'api_keys.col_balance': 'Balance',
    'api_keys.col_daily_spend': "Today's Spend",
    'api_keys.no_data': 'No API Keys found.',
    'api_keys.btn_topup': 'Top Up', 'api_keys.btn_reveal': 'View Key',
    'api_keys.modal_edit_title': 'Edit API Key', 'api_keys.modal_topup_title': 'Top Up',
    'api_keys.modal_create_title': 'Create API Key', 'api_keys.modal_reveal_title': 'View API Key',
    'api_keys.reveal_notice': 'Keep this key secret. Do not share it with unauthorized parties.',
    'api_keys.btn_copy_key': 'Copy',
    'api_keys.lbl_daily_limit': 'Daily Limit', 'api_keys.lbl_daily_limit_hint': 'Leave blank for no limit',
    'api_keys.lbl_qps': 'QPS Limit', 'api_keys.lbl_allowed_models': 'Allowed Logical Models',
    'api_keys.lbl_req_logging': 'Log Request Content', 'api_keys.lbl_res_logging': 'Log Response Content',
    'api_keys.lbl_end_user_hint': '(optional, default x-end-user)',
    'api_keys.lbl_timezone': 'Timezone',
    'api_keys.lbl_timezone_hint': '(IANA format, e.g. Asia/Shanghai, leave blank to keep current)',
    'api_keys.lbl_channel': 'Default Channel',
    'api_keys.lbl_channel_hint': '(optional, default x-channel, e.g. PERSONAL)',
    'api_keys.lbl_initial_balance': 'Initial Balance', 'api_keys.lbl_topup_amount': 'Amount',
    'api_keys.lbl_remark': 'Remark', 'api_keys.btn_confirm_topup': 'Confirm Top Up',
    'api_keys.btn_create': 'Create API Key',
    'api_keys.log_default': 'Default (follow global config)', 'api_keys.log_on': 'On', 'api_keys.log_off': 'Off',
    'api_keys.toast_saved': 'Saved', 'api_keys.toast_topup_fail': 'Top-up failed',
    'api_keys.toast_copied': 'Copied to clipboard', 'api_keys.toast_copy_fail': 'Copy failed',
    'api_keys.no_match_model': 'No matching models',
    'api_keys.confirm_disable': 'Confirm disable {name}?',
    'api_keys.confirm_enable': 'Confirm enable {name}?',
    'api_keys.confirm_delete': 'Confirm delete "{name}"?\n\nThe key will be soft-deleted (billing history retained) and immediately invalidated.',
    'api_keys.toast_disabled': '{name} disabled', 'api_keys.toast_enabled': '{name} enabled',
    'api_keys.toast_deleted': '"{name}" permanently deleted',
    'logical.title': 'Logical Models', 'logical.add': 'New Logical Model',
    'logical.no_desc': 'No description', 'logical.no_data': 'No models found.',
    'logical.btn_routes': 'Routes',
    'logical.modal_edit_title': 'Edit Logical Model', 'logical.modal_create_title': 'Create Logical Model',
    'logical.modal_routes_title': 'Route Rules',
    'logical.lbl_model_name': 'Model Name', 'logical.lbl_enable': 'Enable',
    'logical.btn_add_route': 'Add Route',
    'logical.modal_create_route_title': 'Add Route', 'logical.modal_edit_route_title': 'Edit Route',
    'logical.lbl_priority': 'Priority', 'logical.lbl_weight': 'Weight',
    'logical.lbl_fallback': 'Fallback Route', 'logical.btn_create_route': 'Create Route',
    'logical.col_priority': 'Priority', 'logical.col_weight': 'Weight',
    'logical.col_fallback': 'Fallback', 'logical.col_health': 'Health',
    'logical.fallback_yes': 'Yes', 'logical.fallback_no': 'No',
    'logical.health_ok': 'Healthy', 'logical.health_degraded': 'Degraded',
    'logical.degraded_auth': 'Auth Failed', 'logical.degraded_quota': 'Quota Exhausted',
    'logical.degraded_unavailable': 'Unavailable', 'logical.degraded_error': 'Error',
    'logical.btn_recover': 'Recover Now', 'logical.no_routes': 'No routes found.',
    'logical.confirm_delete': 'Confirm delete logical model "{name}"?\n\nMake sure all routes under this model are deleted first.',
    'logical.confirm_delete_route': 'Confirm delete this route?',
    'logical.recover_failed': 'Recovery failed', 'logical.request_failed': 'Request failed, please retry',
    'logical.toast_deleted': 'Deleted',
    'providers.title': 'Downstream Providers', 'providers.add': 'New Provider',
    'providers.no_data': 'No providers found.',
    'providers.btn_copy': 'Copy',
    'providers.modal_edit_title': 'Edit Provider', 'providers.modal_create_title': 'New Provider',
    'providers.lbl_upstream_model': 'Upstream Model Name',
    'providers.lbl_oai_hint': '(at least one of OAI/Anthropic required)',
    'providers.lbl_ant_hint': '(at least one of OAI/Anthropic required)',
    'providers.lbl_new_api_key': 'New API Key', 'providers.lbl_no_update': 'Leave blank to keep current',
    'providers.lbl_input_price': 'Input Price / 1M', 'providers.lbl_output_price': 'Output Price / 1M',
    'providers.lbl_prompt_cache': 'Supports Prompt Cache', 'providers.lbl_timeout': 'Timeout (s)',
    'providers.lbl_enable': 'Enable', 'providers.btn_create': 'Create Provider',
    'providers.validate_endpoint': 'At least one of OpenAI or Anthropic Endpoint is required',
    'providers.confirm_delete': 'Confirm delete Provider "{name}"?\n\nIt will be soft-deleted (history retained). Remove associated routes first.',
    'providers.toast_deleted': 'Deleted',
    'requests.title': 'Request Logs', 'requests.back': 'Back to Dashboard',
    'requests.lbl_status': 'Request Status', 'requests.lbl_time_range': 'Time Range',
    'requests.opt_all': 'All', 'requests.opt_success': 'Success',
    'requests.opt_failed': 'Failed (≥400)', 'requests.opt_error': 'Error (≥500)',
    'requests.preset_1h': 'Last 1h', 'requests.preset_6h': 'Last 6h',
    'requests.preset_24h': 'Last 24h', 'requests.preset_7d': 'Last 7d',
    'requests.clear': 'Clear', 'requests.to': 'to', 'requests.col_cost': 'Cost',
    'requests.btn_detail': 'Details', 'requests.no_data': 'No logs found.',
    'requests.prev': 'Previous', 'requests.next': 'Next',
    'detail.title': 'Request Details', 'detail.back': 'Back to List',
    'detail.status_success': 'Success', 'detail.status_fail': 'Failed',
    'detail.section_basic': 'Basic Info', 'detail.section_usage': 'Usage & Cost',
    'detail.lbl_logical_model': 'Logical Model', 'detail.lbl_provider_model': 'Provider Model',
    'detail.lbl_call_type': 'Call Type', 'detail.lbl_protocol': 'Provider Protocol',
    'detail.lbl_upstream_id': 'Upstream Request ID', 'detail.lbl_end_user': 'End User',
    'detail.lbl_input': 'Input', 'detail.lbl_reasoning': '↳ Reasoning',
    'detail.lbl_cached': '↳ Cached', 'detail.lbl_output': 'Output',
    'detail.lbl_cache_write': 'Cache Write', 'detail.lbl_cost_input': 'Input Cost',
    'detail.lbl_cost_output': 'Output Cost', 'detail.lbl_cost_cache_read': 'Cache Read Cost',
    'detail.lbl_cost_cache_write': 'Cache Write Cost', 'detail.lbl_total_cost': 'Total Cost',
    'detail.no_usage': 'No usage records', 'detail.tab_request': 'Request Body',
    'detail.tab_response': 'Response / Error',
    'detail.no_request': 'Request body not logged', 'detail.no_response': 'Response body not logged',
    'billing.title': 'Billing & Ledger', 'billing.all_keys': 'All API Keys',
    'billing.btn_filter': 'Filter', 'billing.btn_reset': 'Reset',
    'billing.ledger_title': 'Ledger History', 'billing.search_remark': 'Search remarks...',
    'billing.all_types': 'All Types', 'billing.topup_label': 'Top Up (topup)',
    'billing.charge_label': 'Charge (charge)', 'billing.adjust_label': 'Adjust (adjust)',
    'billing.refund_label': 'Refund (refund)', 'billing.no_ledger': 'No ledger entries found.',
    'billing.col_type': 'Type', 'billing.col_amount': 'Amount',
    'billing.col_before': 'Before', 'billing.col_after': 'After', 'billing.col_remark': 'Remark',
    'billing.usage_title': 'Usage Cost Records', 'billing.search_request': 'Search Request ID...',
    'billing.no_usage': 'No usage records found.',
    'billing.daily_title': 'Daily Cost Summary', 'billing.search_daily': 'Search date or API Key...',
    'billing.no_daily': 'No daily summaries found.',
    'billing.col_requests': 'Requests', 'billing.col_cost': 'Total Cost',
    'statistics.title': 'Request Statistics', 'statistics.lbl_date_range': 'Date Range',
    'statistics.preset_today': 'Today', 'statistics.preset_7d': 'Last 7 days',
    'statistics.preset_30d': 'Last 30 days', 'statistics.preset_month': 'This Month',
    'statistics.preset_year': 'This Year', 'statistics.clear': 'Clear', 'statistics.to': 'to',
    'statistics.lbl_granularity': 'Granularity', 'statistics.lbl_group_by': 'Group By',
    'statistics.group_none': 'No Grouping', 'statistics.btn_query': 'Query',
    'statistics.card_total': 'Total Requests', 'statistics.card_success': 'Success Rate',
    'statistics.card_tokens': 'Total Tokens', 'statistics.card_cost': 'Total Cost (USD)',
    'statistics.table_title': 'Detailed Data',
    'statistics.col_date': 'Date', 'statistics.col_requests': 'Requests',
    'statistics.col_success': 'Success', 'statistics.col_failed': 'Failed',
    'statistics.col_success_rate': 'Success Rate', 'statistics.col_cost': 'Cost (USD)',
    'statistics.no_data': 'No data available.', 'statistics.exact_match': 'Exact match...',
    'playground.lbl_config': 'Request Config', 'playground.lbl_api_type': 'API Type',
    'playground.btn_send': 'Send Request', 'playground.btn_clear': 'Clear',
    'playground.tab_request': 'Request Body', 'playground.tab_response': 'Response Body',
    'playground.lbl_elapsed': 'Elapsed:', 'playground.status_unsent': 'Not Sent',
    'playground.status_sending': 'Sending', 'playground.status_stream': 'Streaming',
    'playground.no_payload': 'GET request, no body',
    'playground.waiting': 'Waiting for request...',
    'playground.json_error': 'Invalid JSON format, please check the payload',
    'playground.oai_protocol': 'OpenAI Protocol', 'playground.ant_protocol': 'Anthropic Protocol',
    'playground.status_err': 'Error',
    'docs.title': 'API Documentation', 'docs.copy': 'Copy',
    'docs.toc': 'Contents', 'docs.example_code': 'Example Code',
    'docs.section_api': 'API Integration', 'docs.section_dev': 'Developer Tools',
    'docs.section_ops': 'Operations Guide',
    'docs.toc_api': 'API Integration', 'docs.toc_req_headers': 'Request Headers',
    'docs.toc_oai_chat': 'OpenAI Chat Completions',
    'docs.toc_oai_models': 'OpenAI List Models',
    'docs.toc_oai_emb': 'OpenAI Embeddings',
    'docs.toc_ant_msg': 'Anthropic Messages',
    'docs.toc_ant_models': 'Anthropic List Models',
    'docs.toc_dev': 'Developer Tools', 'docs.toc_claude_code': 'Claude Code',
    'docs.toc_ops': 'Operations Guide', 'docs.toc_ops_guide': 'Quick Start Guide',
    'docs.toc_routing': 'Routing Rules',
  };

  var TRANSLATIONS = { zh: ZH, en: EN };
  var rerenderCallbacks = [];
  var currentLang;

  function detectLang() {
    // Cookie is set by setLang() so the server can render the correct language
    // on the next page load, eliminating flash of untranslated content (FOUC)
    var cookies = document.cookie.split(';').reduce(function (acc, c) {
      var p = c.trim().split('='); acc[decodeURIComponent(p[0])] = decodeURIComponent(p[1] || ''); return acc;
    }, {});
    var cookie = cookies[STORAGE_KEY];
    if (cookie === 'zh' || cookie === 'en') return cookie;
    var stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'zh' || stored === 'en') return stored;
    var nav = (navigator.language || '').toLowerCase();
    return nav.startsWith('zh') ? 'zh' : 'en';
  }

  currentLang = detectLang();

  function t(key) {
    var dict = TRANSLATIONS[currentLang] || ZH;
    return dict[key] !== undefined ? dict[key] : (ZH[key] !== undefined ? ZH[key] : key);
  }

  function apply() {
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      var v = t(el.getAttribute('data-i18n'));
      if (v !== undefined) el.textContent = v;
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
      var v = t(el.getAttribute('data-i18n-placeholder'));
      if (v !== undefined) el.placeholder = v;
    });
    document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      var v = t(el.getAttribute('data-i18n-title'));
      if (v !== undefined) el.title = v;
    });
    document.querySelectorAll('[data-i18n-label]').forEach(function (el) {
      var v = t(el.getAttribute('data-i18n-label'));
      if (v !== undefined) el.label = v;
    });
    // Server-side pagination spans with data-i18n-page + data-i18n-total
    document.querySelectorAll('[data-i18n-page]').forEach(function (el) {
      var cur = el.getAttribute('data-i18n-page');
      var total = el.getAttribute('data-i18n-total');
      el.textContent = t('common.page_info').replace('{cur}', cur).replace('{total}', total);
    });
    // Language blocks for docs bilingual sections
    document.querySelectorAll('[data-lang]').forEach(function (el) {
      el.style.display = el.getAttribute('data-lang') === currentLang ? '' : 'none';
    });
    // Toggle button label
    var btn = document.getElementById('lang-toggle-btn');
    if (btn) btn.textContent = currentLang === 'zh' ? 'EN' : '中文';
    // html lang attr
    document.documentElement.lang = currentLang === 'zh' ? 'zh-CN' : 'en';
  }

  function setLang(lang) {
    currentLang = lang;
    localStorage.setItem(STORAGE_KEY, lang);
    // Write cookie so the server renders the correct language on next page load
    document.cookie = STORAGE_KEY + '=' + lang + '; path=/; max-age=31536000; SameSite=Lax';
    apply();
    rerenderCallbacks.forEach(function (cb) { try { cb(); } catch (e) {} });
  }

  // Apply immediately then again after DOM ready
  apply();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', apply);
  }

  window.t = t;
  window.i18n = {
    get lang() { return currentLang; },
    t: t,
    apply: apply,
    setLang: setLang,
    onLangChange: function (fn) { rerenderCallbacks.push(fn); }
  };
})();
