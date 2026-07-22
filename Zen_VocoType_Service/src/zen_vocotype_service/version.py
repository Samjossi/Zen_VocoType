"""服务端软件版本号（health 响应用，单一出处）。"""

#: 服务端软件版本，与 pyproject.toml 保持一致（🔴 两位数格式 major.minor，
#: 未来版本延续 1.1、1.2、2.0…，不再使用三段式）
SERVICE_VERSION: str = "1.0"
