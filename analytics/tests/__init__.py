def process_analytics_test_data(data: dict) -> dict:
    """
    处理分析测试数据的业务逻辑。
    
    :param data: 输入的数据字典
    :return: 包含处理状态和基本统计信息的结果字典
    """
    if not data:
        return {"status": "empty", "count": 0}
    
    return {
        "status": "success",
        "count": len(data),
        "keys": list(data.keys())
    }
