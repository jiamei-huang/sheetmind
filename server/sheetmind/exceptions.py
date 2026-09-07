"""
统一异常定义
避免错误信息泄露内部细节，提供一致的异常处理模式
"""


class SheetMindException(Exception):
    """SheetMind 基础异常类"""

    def __init__(self, message: str, error_code: str = None, internal_detail: str = None):
        """
        Args:
            message: 对外展示的错误信息（用户可见）
            error_code: 错误码，用于前端区分错误类型
            internal_detail: 内部详情（仅记录日志，不返回给用户）
        """
        self.message = message
        self.error_code = error_code
        self.internal_detail = internal_detail
        super().__init__(self.message)


class ProjectNotFoundError(SheetMindException):
    """项目不存在"""
    pass


class TaskNotFoundError(SheetMindException):
    """任务不存在"""
    pass


class FileParseError(SheetMindException):
    """文件解析失败"""
    pass


class FileTooLargeError(SheetMindException):
    """文件过大"""
    pass


class InvalidFileTypeError(SheetMindException):
    """不支持的文件类型"""
    pass


class AIAnalysisError(SheetMindException):
    """AI 分析失败"""
    pass


class DatabaseError(SheetMindException):
    """数据库操作失败"""
    pass


class ValidationError(SheetMindException):
    """数据验证失败"""
    pass


def safe_error_message(exc: Exception) -> str:
    """
    获取安全的错误信息（不泄露内部细节）

    Args:
        exc: 异常对象

    Returns:
        安全的、可对外展示的错误信息
    """
    if isinstance(exc, SheetMindException):
        return exc.message

    # 非自定义异常：返回通用信息，避免泄露堆栈、路径等
    return "操作失败，请稍后重试"
