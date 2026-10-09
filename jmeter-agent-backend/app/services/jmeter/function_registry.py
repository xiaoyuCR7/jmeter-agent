"""JMeter 函数元数据注册表"""
from typing import Dict, List, Any


FUNCTION_REGISTRY: Dict[str, Dict[str, Any]] = {
    "__time": {
        "label": "time - 返回当前时间",
        "description": "返回以各种格式表示的当前时间",
        "params": [
            {"name": "format", "label": "格式", "type": "text", "placeholder": "yyyy/MM/dd HH:mm:ss", "required": False},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__time(yyyy/MM/dd HH:mm:ss,)} → 2024/01/15 10:30:00",
    },
    "__Random": {
        "label": "Random - 随机整数",
        "description": "返回指定范围内的随机整数",
        "params": [
            {"name": "min", "label": "最小值", "type": "number", "required": True},
            {"name": "max", "label": "最大值", "type": "number", "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__Random(1,100,)} → 42",
    },
    "__RandomString": {
        "label": "RandomString - 随机字符串",
        "description": "返回指定长度的随机字符串",
        "params": [
            {"name": "length", "label": "长度", "type": "number", "required": True},
            {"name": "chars", "label": "字符集", "type": "text", "placeholder": "abcdefghijklmnopqrstuvwxyz", "required": False},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__RandomString(10,abcdef,)} → abcdfeabcd",
    },
    "__UUID": {
        "label": "UUID - 生成UUID",
        "description": "生成一个通用唯一识别码",
        "params": [],
        "example": "${__UUID} → 8e22b41b-5c7f-4c6d-9c3e-1a2b3c4d5e6f",
    },
    "__threadNum": {
        "label": "threadNum - 线程号",
        "description": "返回当前线程的编号",
        "params": [],
        "example": "${__threadNum} → 3",
    },
    "__machineName": {
        "label": "machineName - 机器名",
        "description": "返回执行机器的 hostname",
        "params": [],
        "example": "${__machineName} → my-pc",
    },
    "__machineIP": {
        "label": "machineIP - 机器IP",
        "description": "返回执行机器的 IP 地址",
        "params": [],
        "example": "${__machineIP} → 192.168.1.100",
    },
    "__TestPlanName": {
        "label": "TestPlanName - 测试计划名",
        "description": "返回当前测试计划的名称（文件名）",
        "params": [],
        "example": "${__TestPlanName} → my_test.jmx",
    },
    "__P": {
        "label": "P - 获取属性（简写）",
        "description": "获取 JMeter 属性值，可通过命令行 -J 传入",
        "params": [
            {"name": "property", "label": "属性名", "type": "text", "required": True},
            {"name": "default", "label": "默认值", "type": "text", "required": False},
        ],
        "example": "${__P(host,localhost)} → localhost",
    },
    "__property": {
        "label": "property - 获取属性",
        "description": "获取 JMeter 属性值，支持变量存储",
        "params": [
            {"name": "property", "label": "属性名", "type": "text", "required": True},
            {"name": "default", "label": "默认值", "type": "text", "required": False},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__property(host,,)} → localhost",
    },
    "__eval": {
        "label": "eval - 求值",
        "description": "对字符串中的变量引用进行求值",
        "params": [
            {"name": "expression", "label": "表达式", "type": "text", "placeholder": "${var1}+${var2}", "required": True},
        ],
        "example": "${__eval(${a}+${b})} → 15",
    },
    "__V": {
        "label": "V - 嵌套变量求值",
        "description": "用于求值嵌套变量，如 ${__V(var_${i})}",
        "params": [
            {"name": "expression", "label": "表达式", "type": "text", "placeholder": "var_${i}", "required": True},
            {"name": "default", "label": "默认值", "type": "text", "required": False},
        ],
        "example": "${__V(var_${__threadNum},)} → var_1 的值",
    },
    "__intSum": {
        "label": "intSum - 整数求和",
        "description": "对多个整数求和",
        "params": [
            {"name": "a", "label": "数值1", "type": "number", "required": True},
            {"name": "b", "label": "数值2", "type": "number", "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__intSum(10,20,)} → 30",
    },
    "__counter": {
        "label": "counter - 计数器",
        "description": "生成递增计数，每个线程独立或全局共享",
        "params": [
            {"name": "perThread", "label": "每个线程独立计数", "type": "select", "options": [{"value": "TRUE", "label": "是"}, {"value": "FALSE", "label": "否"}], "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__counter(FALSE,)} → 1, 2, 3...",
    },
    "__CSVRead": {
        "label": "CSVRead - CSV读取",
        "description": "从 CSV 文件中读取指定列的数据（不推荐，建议使用 CSV Data Set Config）",
        "params": [
            {"name": "filename", "label": "文件名", "type": "text", "required": True},
            {"name": "column", "label": "列号/列名", "type": "text", "placeholder": "0", "required": True},
        ],
        "example": "${__CSVRead(data.csv,0)} → 第一列值",
    },
    "__digest": {
        "label": "digest - 摘要/哈希",
        "description": "对字符串进行哈希运算（MD5, SHA-1, SHA-256 等）",
        "params": [
            {"name": "algorithm", "label": "算法", "type": "select", "options": [{"value": "MD5", "label": "MD5"}, {"value": "SHA-1", "label": "SHA-1"}, {"value": "SHA-256", "label": "SHA-256"}], "required": True},
            {"name": "string", "label": "字符串", "type": "text", "required": True},
            {"name": "salt", "label": "盐值", "type": "text", "required": False},
        ],
        "example": "${__digest(MD5,password,,)} → 5f4dcc3b5aa765d61d8327deb882cf99",
    },
    "__urlencode": {
        "label": "urlencode - URL编码",
        "description": "对字符串进行 URL 编码",
        "params": [
            {"name": "string", "label": "字符串", "type": "text", "required": True},
        ],
        "example": "${__urlencode(hello world)} → hello+world",
    },
    "__urldecode": {
        "label": "urldecode - URL解码",
        "description": "对字符串进行 URL 解码",
        "params": [
            {"name": "string", "label": "字符串", "type": "text", "required": True},
        ],
        "example": "${__urldecode(hello+world)} → hello world",
    },
    "__split": {
        "label": "split - 字符串分割",
        "description": "将字符串按分隔符分割，存入变量数组",
        "params": [
            {"name": "string", "label": "字符串", "type": "text", "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": True},
            {"name": "separator", "label": "分隔符", "type": "text", "placeholder": ",", "required": False},
        ],
        "example": "${__split(a,b,c,VAR,,)} → VAR_1=a, VAR_2=b, VAR_3=c",
    },
    "__substring": {
        "label": "substring - 子字符串",
        "description": "从字符串中提取子串",
        "params": [
            {"name": "string", "label": "字符串", "type": "text", "required": True},
            {"name": "begin", "label": "开始索引", "type": "number", "required": True},
            {"name": "end", "label": "结束索引", "type": "number", "required": False},
        ],
        "example": "${__substring(hello world,0,5)} → hello",
    },
    "__timeShift": {
        "label": "timeShift - 时间偏移",
        "description": "返回偏移后的时间",
        "params": [
            {"name": "format", "label": "格式", "type": "text", "placeholder": "yyyy/MM/dd HH:mm:ss", "required": False},
            {"name": "date", "label": "基准时间", "type": "text", "placeholder": "now", "required": False},
            {"name": "shift", "label": "偏移量", "type": "text", "placeholder": "PT1H30M", "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__timeShift(yyyy/MM/dd HH:mm:ss,,PT1H,)} → 当前时间+1小时",
    },
    "__dateTimeConvert": {
        "label": "dateTimeConvert - 时间格式转换",
        "description": "将时间从一种格式转换为另一种格式",
        "params": [
            {"name": "date", "label": "源时间", "type": "text", "required": True},
            {"name": "sourceFormat", "label": "源格式", "type": "text", "placeholder": "yyyy/MM/dd HH:mm:ss", "required": True},
            {"name": "targetFormat", "label": "目标格式", "type": "text", "placeholder": "dd/MM/yyyy", "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__dateTimeConvert(2024/01/15,yyyy/MM/dd,dd/MM/yyyy,)} → 15/01/2024",
    },
    "__escapeHtml": {
        "label": "escapeHtml - HTML编码",
        "description": "对字符串进行 HTML 实体编码",
        "params": [
            {"name": "string", "label": "字符串", "type": "text", "required": True},
        ],
        "example": "${__escapeHtml(<div>test</div>)} → &lt;div&gt;test&lt;/div&gt;",
    },
    "__fileToString": {
        "label": "fileToString - 文件转字符串",
        "description": "读取文件内容作为字符串",
        "params": [
            {"name": "filename", "label": "文件名", "type": "text", "required": True},
            {"name": "encoding", "label": "编码", "type": "text", "placeholder": "UTF-8", "required": False},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__fileToString(data.json,UTF-8,)} → 文件内容",
    },
    "__log": {
        "label": "log - 日志输出",
        "description": "将消息输出到 jmeter.log",
        "params": [
            {"name": "message", "label": "消息", "type": "text", "required": True},
            {"name": "level", "label": "级别", "type": "select", "options": [{"value": "INFO", "label": "INFO"}, {"value": "DEBUG", "label": "DEBUG"}, {"value": "WARN", "label": "WARN"}, {"value": "ERROR", "label": "ERROR"}], "required": False},
            {"name": "throwable", "label": "异常", "type": "text", "required": False},
        ],
        "example": "${__log(当前变量值=${var},INFO)} → 输出到日志",
    },
    "__logn": {
        "label": "logn - 日志输出（无变量）",
        "description": "将消息输出到 jmeter.log，不返回值",
        "params": [
            {"name": "message", "label": "消息", "type": "text", "required": True},
            {"name": "level", "label": "级别", "type": "select", "options": [{"value": "INFO", "label": "INFO"}, {"value": "DEBUG", "label": "DEBUG"}, {"value": "WARN", "label": "WARN"}, {"value": "ERROR", "label": "ERROR"}], "required": False},
        ],
        "example": "${__logn(开始执行)} → 输出到日志",
    },
    "__longSum": {
        "label": "longSum - 长整数求和",
        "description": "对多个长整数求和",
        "params": [
            {"name": "a", "label": "数值1", "type": "number", "required": True},
            {"name": "b", "label": "数值2", "type": "number", "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__longSum(10000000000,20000000000,)} → 30000000000",
    },
    "__strLen": {
        "label": "strLen - 字符串长度",
        "description": "返回字符串的长度",
        "params": [
            {"name": "string", "label": "字符串", "type": "text", "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__strLen(hello,)} → 5",
    },
    "__uppercase": {
        "label": "uppercase - 转大写",
        "description": "将字符串转为大写",
        "params": [
            {"name": "string", "label": "字符串", "type": "text", "required": True},
        ],
        "example": "${__uppercase(hello)} → HELLO",
    },
    "__lowercase": {
        "label": "lowercase - 转小写",
        "description": "将字符串转为小写",
        "params": [
            {"name": "string", "label": "字符串", "type": "text", "required": True},
        ],
        "example": "${__lowercase(HELLO)} → hello",
    },
    "__trim": {
        "label": "trim - 去除空白",
        "description": "去除字符串两端的空白字符",
        "params": [
            {"name": "string", "label": "字符串", "type": "text", "required": True},
        ],
        "example": "${__trim(  hello  )} → hello",
    },
    "__RandomFromMultipleVars": {
        "label": "RandomFromMultipleVars - 多变量随机",
        "description": "从多个变量中随机选取一个",
        "params": [
            {"name": "vars", "label": "变量名（竖线分隔）", "type": "text", "placeholder": "var1|var2|var3", "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__RandomFromMultipleVars(a|b|c,)} → a 或 b 或 c",
    },
    "__iterationNum": {
        "label": "iterationNum - 迭代次数",
        "description": "返回当前线程的迭代次数",
        "params": [
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__iterationNum} → 1",
    },
    "__isVarDefined": {
        "label": "isVarDefined - 变量是否定义",
        "description": "检查变量是否已定义",
        "params": [
            {"name": "variable", "label": "变量名", "type": "text", "required": True},
        ],
        "example": "${__isVarDefined(var)} → true/false",
    },
    "__XPath": {
        "label": "XPath - XPath提取",
        "description": "从 XML 文件中提取 XPath 值",
        "params": [
            {"name": "filename", "label": "XML文件名", "type": "text", "required": True},
            {"name": "xpath", "label": "XPath表达式", "type": "text", "required": True},
            {"name": "default", "label": "默认值", "type": "text", "required": False},
        ],
        "example": "${__XPath(data.xml,/root/item[1]/name,)} → 提取的值",
    },
    "__base64Encode": {
        "label": "base64Encode - Base64编码",
        "description": "对字符串进行 Base64 编码",
        "params": [
            {"name": "string", "label": "字符串", "type": "text", "required": True},
        ],
        "example": "${__base64Encode(hello)} → aGVsbG8=",
    },
    "__base64Decode": {
        "label": "base64Decode - Base64解码",
        "description": "对 Base64 字符串进行解码",
        "params": [
            {"name": "string", "label": "Base64字符串", "type": "text", "required": True},
        ],
        "example": "${__base64Decode(aGVsbG8=)} → hello",
    },
    "__StringFromFile": {
        "label": "StringFromFile - 逐行读取文件",
        "description": "从文件中逐行读取，每次调用返回下一行",
        "params": [
            {"name": "filename", "label": "文件名", "type": "text", "required": True},
            {"name": "encoding", "label": "编码", "type": "text", "placeholder": "UTF-8", "required": False},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__StringFromFile(data.txt,UTF-8,)} → 文件第一行",
    },
    "__BeanShell": {
        "label": "BeanShell - BeanShell脚本",
        "description": "执行 BeanShell 脚本并返回结果",
        "params": [
            {"name": "script", "label": "脚本", "type": "text", "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__BeanShell(return \"hello\";,)} → hello",
    },
    "__javaScript": {
        "label": "javaScript - JavaScript脚本",
        "description": "执行 JavaScript 脚本并返回结果",
        "params": [
            {"name": "script", "label": "脚本", "type": "text", "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__javaScript(\"hello\",)} → hello",
    },
    "__groovy": {
        "label": "groovy - Groovy脚本",
        "description": "执行 Groovy 脚本并返回结果",
        "params": [
            {"name": "script", "label": "脚本", "type": "text", "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__groovy(return \"hello\",)} → hello",
    },
    "__jexl2": {
        "label": "jexl2 - JEXL2表达式",
        "description": "执行 JEXL2 表达式",
        "params": [
            {"name": "expression", "label": "表达式", "type": "text", "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__jexl2(1+2,)} → 3",
    },
    "__jexl3": {
        "label": "jexl3 - JEXL3表达式",
        "description": "执行 JEXL3 表达式",
        "params": [
            {"name": "expression", "label": "表达式", "type": "text", "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__jexl3(1+2,)} → 3",
    },
    "__char": {
        "label": "char - 字符生成",
        "description": "从十进制或十六进制值生成字符",
        "params": [
            {"name": "codes", "label": "字符码（逗号分隔）", "type": "text", "required": True},
        ],
        "example": "${__char(65,66,67)} → ABC",
    },
    "__changeCase": {
        "label": "changeCase - 大小写转换",
        "description": "转换字符串大小写",
        "params": [
            {"name": "string", "label": "字符串", "type": "text", "required": True},
            {"name": "mode", "label": "模式", "type": "select", "options": [{"value": "UPPER", "label": "大写"}, {"value": "LOWER", "label": "小写"}, {"value": "CAPITALIZE", "label": "首字母大写"}], "required": True},
        ],
        "example": "${__changeCase(hello,UPPER)} → HELLO",
    },
    "__evalVar": {
        "label": "evalVar - 变量表达式求值",
        "description": "对包含变量引用的字符串求值并存储到变量",
        "params": [
            {"name": "expression", "label": "表达式", "type": "text", "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "required": True},
        ],
        "example": "${__evalVar(${a}+${b},result)}",
    },
    "__escapeXml": {
        "label": "escapeXml - XML编码",
        "description": "对字符串进行 XML 实体编码",
        "params": [
            {"name": "string", "label": "字符串", "type": "text", "required": True},
        ],
        "example": "${__escapeXml(<tag>)} → &lt;tag&gt;",
    },
    "__escapeOroRegexpChars": {
        "label": "escapeOroRegexpChars - 正则转义",
        "description": "转义 ORO 正则表达式特殊字符",
        "params": [
            {"name": "string", "label": "字符串", "type": "text", "required": True},
        ],
        "example": "${__escapeOroRegexpChars(1.2)} → 1\\.2",
    },
    "__unEscape": {
        "label": "unEscape - 反转义",
        "description": "反转义 Java 字符串字面量",
        "params": [
            {"name": "string", "label": "字符串", "type": "text", "required": True},
        ],
        "example": "${__unEscape(\\n)} → 换行",
    },
    "__unescapeHtml": {
        "label": "unescapeHtml - HTML解码",
        "description": "对 HTML 实体进行解码",
        "params": [
            {"name": "string", "label": "字符串", "type": "text", "required": True},
        ],
        "example": "${__unescapeHtml(&lt;div&gt;)} → <div>",
    },
    "__isPropDefined": {
        "label": "isPropDefined - 属性是否定义",
        "description": "检查 JMeter 属性是否已定义",
        "params": [
            {"name": "property", "label": "属性名", "type": "text", "required": True},
        ],
        "example": "${__isPropDefined(host)} → true/false",
    },
    "__setProperty": {
        "label": "setProperty - 设置属性",
        "description": "设置 JMeter 属性值",
        "params": [
            {"name": "property", "label": "属性名", "type": "text", "required": True},
            {"name": "value", "label": "值", "type": "text", "required": True},
        ],
        "example": "${__setProperty(host,localhost)}",
    },
    "__RandomDate": {
        "label": "RandomDate - 随机日期",
        "description": "生成指定范围内的随机日期",
        "params": [
            {"name": "format", "label": "格式", "type": "text", "placeholder": "yyyy-MM-dd", "required": False},
            {"name": "startDate", "label": "开始日期", "type": "text", "required": True},
            {"name": "endDate", "label": "结束日期", "type": "text", "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__RandomDate(yyyy-MM-dd,2024-01-01,2024-12-31,)}",
    },
    "__regexFunction": {
        "label": "regexFunction - 正则函数",
        "description": "使用正则表达式处理字符串",
        "params": [
            {"name": "regex", "label": "正则表达式", "type": "text", "required": True},
            {"name": "template", "label": "模板", "type": "text", "placeholder": "$1$", "required": True},
            {"name": "string", "label": "字符串", "type": "text", "required": True},
            {"name": "variable", "label": "变量名", "type": "text", "placeholder": "VAR", "required": False},
        ],
        "example": "${__regexFunction(\\d+,ID: $0$,order123,)}",
    },
    "__samplerName": {
        "label": "samplerName - 取样器名称",
        "description": "返回当前取样器的名称",
        "params": [],
        "example": "${__samplerName} → HTTP Request",
    },
    "__StringToFile": {
        "label": "StringToFile - 字符串写入文件",
        "description": "将字符串写入文件",
        "params": [
            {"name": "filename", "label": "文件名", "type": "text", "required": True},
            {"name": "string", "label": "字符串", "type": "text", "required": True},
            {"name": "append", "label": "追加", "type": "select", "options": [{"value": "false", "label": "否"}, {"value": "true", "label": "是"}], "required": False},
        ],
        "example": "${__StringToFile(output.txt,hello,false)}",
    },
    "__threadGroupName": {
        "label": "threadGroupName - 线程组名称",
        "description": "返回当前线程组的名称",
        "params": [],
        "example": "${__threadGroupName} → Thread Group",
    },
    "__variable": {
        "label": "variable - 获取变量",
        "description": "获取变量值（与 ${var} 类似）",
        "params": [
            {"name": "variable", "label": "变量名", "type": "text", "required": True},
            {"name": "default", "label": "默认值", "type": "text", "required": False},
        ],
        "example": "${__variable(token,)} → token 的值",
    },
}


def get_all_functions() -> List[Dict[str, Any]]:
    """获取所有函数元数据列表"""
    result = []
    for name, meta in FUNCTION_REGISTRY.items():
        result.append({
            "name": name,
            **meta,
        })
    return result


def get_function_meta(name: str) -> Dict[str, Any]:
    """获取单个函数元数据"""
    return FUNCTION_REGISTRY.get(name, {})
