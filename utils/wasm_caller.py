import time
import wasmtime
from pathlib import Path
import json
import subprocess
import tempfile
import os
from typing import Optional


class NodeJSBridgeCaller:
    """使用Node.js作为桥接调用WASM模块（推荐方案）"""
    
    def __init__(self, pkg_dir="pkg"):
        self.pkg_dir = Path(pkg_dir).resolve()
        if not self.pkg_dir.exists():
            raise FileNotFoundError(f"pkg目录不存在: {pkg_dir}")
        
        # 检查必要文件
        required_files = ["PumpPillWasm.js", "PumpPillWasm_bg.wasm"]
        for file in required_files:
            if not (self.pkg_dir / file).exists():
                raise FileNotFoundError(f"必要文件不存在: {file}")
    
    def encrypt_data(self, screen_name: str, user_id: str) -> str:
        """
        通过Node.js调用encrypt_data函数
        
        Args:
            screen_name (str): 屏幕名称/用户名
            user_id (str): 用户ID
            
        Returns:
            str: 加密后的数据
        """
        # 转义字符串以防止注入
        screen_name_escaped = screen_name.replace("'", "\\'").replace("\\", "\\\\")
        user_id_escaped = user_id.replace("'", "\\'").replace("\\", "\\\\")
        
        # 创建Node.js脚本
        node_script = f"""
import fs from 'fs';
import path from 'path';

// 模拟浏览器环境
global.window = {{
    location: {{
        href: 'https://twitter.com',
        origin: 'https://twitter.com',
        protocol: 'https:',
        host: 'twitter.com',
        hostname: 'twitter.com',
        port: '',
        pathname: '/',
        search: '',
        hash: ''
    }},
    navigator: {{
        userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        language: 'en-US',
        languages: ['en-US', 'en'],
        platform: 'Win32',
        vendor: 'Google Inc.',
        appVersion: '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }},
    document: {{
        createElement: () => ({{}}),
        getElementById: () => null,
        querySelector: () => null
    }},
    localStorage: {{
        getItem: () => null,
        setItem: () => {{}},
        removeItem: () => {{}},
        clear: () => {{}}
    }},
    sessionStorage: {{
        getItem: () => null,
        setItem: () => {{}},
        removeItem: () => {{}},
        clear: () => {{}}
    }},
    fetch: () => Promise.resolve({{}}),
    XMLHttpRequest: class {{}},
    WebSocket: class {{}},
    crypto: {{
        getRandomValues: (arr) => {{
            for (let i = 0; i < arr.length; i++) {{
                arr[i] = Math.floor(Math.random() * 256);
            }}
            return arr;
        }},
        subtle: {{
            digest: () => Promise.resolve(new Uint8Array(32)),
            encrypt: () => Promise.resolve(new Uint8Array(32)),
            decrypt: () => Promise.resolve(new Uint8Array(32))
        }}
    }},
    btoa: (str) => Buffer.from(str).toString('base64'),
    atob: (str) => Buffer.from(str, 'base64').toString()
}};

global.document = window.document;
global.navigator = window.navigator;
global.location = window.location;
global.localStorage = window.localStorage;
global.sessionStorage = window.sessionStorage;
global.fetch = window.fetch;
global.XMLHttpRequest = window.XMLHttpRequest;
global.WebSocket = window.WebSocket;
global.crypto = window.crypto;
global.btoa = window.btoa;
global.atob = window.atob;

async function callEncryptData() {{
    try {{
        // 动态导入WASM模块
        const wasmPath = path.join('{self.pkg_dir}', 'PumpPillWasm_bg.wasm');
        const wasmBytes = fs.readFileSync(wasmPath);
        
        // 导入JavaScript包装器
        const {{ default: init, encrypt_data }} = await import('file://{self.pkg_dir}/PumpPillWasm.js');
        
        // 初始化WASM模块
        await init(wasmBytes);
        
        // 调用encrypt_data函数
        const result = encrypt_data('{screen_name_escaped}', '{user_id_escaped}');
        
        console.log(JSON.stringify({{ 
            success: true, 
            result: result 
        }}));
        
    }} catch (error) {{
        console.log(JSON.stringify({{ 
            success: false, 
            error: error.message,
            stack: error.stack 
        }}));
    }}
}}

callEncryptData();
"""
        
        return self._execute_node_script(node_script)
    
    def check_browser_environment(self) -> bool:
        """检查浏览器环境"""
        node_script = f"""
import fs from 'fs';
import path from 'path';

// 模拟浏览器环境
global.window = {{
    location: {{
        href: 'https://twitter.com',
        origin: 'https://twitter.com',
        protocol: 'https:',
        host: 'twitter.com',
        hostname: 'twitter.com',
        port: '',
        pathname: '/',
        search: '',
        hash: ''
    }},
    navigator: {{
        userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        language: 'en-US',
        languages: ['en-US', 'en'],
        platform: 'Win32',
        vendor: 'Google Inc.',
        appVersion: '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }},
    document: {{
        createElement: () => ({{}}),
        getElementById: () => null,
        querySelector: () => null
    }},
    localStorage: {{
        getItem: () => null,
        setItem: () => {{}},
        removeItem: () => {{}},
        clear: () => {{}}
    }},
    sessionStorage: {{
        getItem: () => null,
        setItem: () => {{}},
        removeItem: () => {{}},
        clear: () => {{}}
    }},
    fetch: () => Promise.resolve({{}}),
    XMLHttpRequest: class {{}},
    WebSocket: class {{}},
    crypto: {{
        getRandomValues: (arr) => {{
            for (let i = 0; i < arr.length; i++) {{
                arr[i] = Math.floor(Math.random() * 256);
            }}
            return arr;
        }},
        subtle: {{
            digest: () => Promise.resolve(new Uint8Array(32)),
            encrypt: () => Promise.resolve(new Uint8Array(32)),
            decrypt: () => Promise.resolve(new Uint8Array(32))
        }}
    }},
    btoa: (str) => Buffer.from(str).toString('base64'),
    atob: (str) => Buffer.from(str, 'base64').toString()
}};

global.document = window.document;
global.navigator = window.navigator;
global.location = window.location;
global.localStorage = window.localStorage;
global.sessionStorage = window.sessionStorage;
global.fetch = window.fetch;
global.XMLHttpRequest = window.XMLHttpRequest;
global.WebSocket = window.WebSocket;
global.crypto = window.crypto;
global.btoa = window.btoa;
global.atob = window.atob;

async function callCheckBrowserEnvironment() {{
    try {{
        const wasmPath = path.join('{self.pkg_dir}', 'PumpPillWasm_bg.wasm');
        const wasmBytes = fs.readFileSync(wasmPath);
        
        const {{ default: init, check_browser_environment }} = await import('file://{self.pkg_dir}/PumpPillWasm.js');
        await init(wasmBytes);
        
        const result = check_browser_environment();
        
        console.log(JSON.stringify({{ 
            success: true, 
            result: result 
        }}));
        
    }} catch (error) {{
        console.log(JSON.stringify({{ 
            success: false, 
            error: error.message 
        }}));
    }}
}}

callCheckBrowserEnvironment();
"""
        
        result = self._execute_node_script(node_script)
        return bool(result)
    
    def generate_fake_hash(self, input_str: str) -> str:
        """生成假哈希"""
        input_escaped = input_str.replace("'", "\\'").replace("\\", "\\\\")
        
        node_script = f"""
import fs from 'fs';
import path from 'path';

// 模拟浏览器环境
global.window = {{
    location: {{
        href: 'https://twitter.com',
        origin: 'https://twitter.com',
        protocol: 'https:',
        host: 'twitter.com',
        hostname: 'twitter.com',
        port: '',
        pathname: '/',
        search: '',
        hash: ''
    }},
    navigator: {{
        userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        language: 'en-US',
        languages: ['en-US', 'en'],
        platform: 'Win32',
        vendor: 'Google Inc.',
        appVersion: '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }},
    document: {{
        createElement: () => ({{}}),
        getElementById: () => null,
        querySelector: () => null
    }},
    localStorage: {{
        getItem: () => null,
        setItem: () => {{}},
        removeItem: () => {{}},
        clear: () => {{}}
    }},
    sessionStorage: {{
        getItem: () => null,
        setItem: () => {{}},
        removeItem: () => {{}},
        clear: () => {{}}
    }},
    fetch: () => Promise.resolve({{}}),
    XMLHttpRequest: class {{}},
    WebSocket: class {{}},
    crypto: {{
        getRandomValues: (arr) => {{
            for (let i = 0; i < arr.length; i++) {{
                arr[i] = Math.floor(Math.random() * 256);
            }}
            return arr;
        }},
        subtle: {{
            digest: () => Promise.resolve(new Uint8Array(32)),
            encrypt: () => Promise.resolve(new Uint8Array(32)),
            decrypt: () => Promise.resolve(new Uint8Array(32))
        }}
    }},
    btoa: (str) => Buffer.from(str).toString('base64'),
    atob: (str) => Buffer.from(str, 'base64').toString()
}};

global.document = window.document;
global.navigator = window.navigator;
global.location = window.location;
global.localStorage = window.localStorage;
global.sessionStorage = window.sessionStorage;
global.fetch = window.fetch;
global.XMLHttpRequest = window.XMLHttpRequest;
global.WebSocket = window.WebSocket;
global.crypto = window.crypto;
global.btoa = window.btoa;
global.atob = window.atob;

async function callGenerateFakeHash() {{
    try {{
        const wasmPath = path.join('{self.pkg_dir}', 'PumpPillWasm_bg.wasm');
        const wasmBytes = fs.readFileSync(wasmPath);
        
        const {{ default: init, generate_fake_hash }} = await import('file://{self.pkg_dir}/PumpPillWasm.js');
        await init(wasmBytes);
        
        const result = generate_fake_hash('{input_escaped}');
        
        console.log(JSON.stringify({{ 
            success: true, 
            result: result 
        }}));
        
    }} catch (error) {{
        console.log(JSON.stringify({{ 
            success: false, 
            error: error.message 
        }}));
    }}
}}

callGenerateFakeHash();
"""
        
        return self._execute_node_script(node_script)
    
    def _execute_node_script(self, script: str):
        """执行Node.js脚本并返回结果"""
        temp_file = None
        try:
            # 创建临时文件
            with tempfile.NamedTemporaryFile(mode='w', suffix='.mjs', delete=False) as f:
                f.write(script)
                temp_file = f.name
            
            # 执行Node.js脚本
            result = subprocess.run(
                ['node', temp_file], 
                capture_output=True, 
                text=True,
                cwd=str(self.pkg_dir.parent),  # 设置工作目录
                timeout=30  # 30秒超时
            )
            
            if result.returncode != 0:
                raise RuntimeError(f"Node.js执行失败: {result.stderr}")
            
            # 解析结果
            try:
                output = json.loads(result.stdout.strip())
            except json.JSONDecodeError as e:
                raise RuntimeError(f"解析Node.js输出失败: {result.stdout}")
            
            if output['success']:
                return output['result']
            else:
                raise RuntimeError(f"WASM调用失败: {output['error']}")
                
        except subprocess.TimeoutExpired:
            raise RuntimeError("Node.js脚本执行超时")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Node.js执行失败: {e}")
        finally:
            # 清理临时文件
            if temp_file and os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except OSError:
                    pass


class PumpPillAPI:
    """简化的API封装类"""
    
    def __init__(self, pkg_dir="./pkg", use_nodejs=True):
        """
        初始化API
        
        Args:
            pkg_dir: WASM包目录路径
            use_nodejs: 是否使用Node.js桥接（推荐True）
        """
        self.caller = NodeJSBridgeCaller(pkg_dir)
    
    def encrypt_data(self, username: str, user_id: str) -> str:
        """
        加密数据
        
        Args:
            username: 用户名
            user_id: 用户ID
            
        Returns:
            加密后的字符串
        """
        return self.caller.encrypt_data(username, user_id)
    
    def check_browser_environment(self) -> bool:
        """检查是否为浏览器环境"""
        if hasattr(self.caller, 'check_browser_environment'):
            return self.caller.check_browser_environment()
        return False
    
    def generate_fake_hash(self, input_str: str) -> str:
        """生成假哈希"""
        if hasattr(self.caller, 'generate_fake_hash'):
            return self.caller.generate_fake_hash(input_str)
        return ""


def test_wasm_functions():
    """测试函数"""
    try:
        # 初始化API，强制使用Node.js桥接
        api = PumpPillAPI("pkg", use_nodejs=True)
        
        # 测试encrypt_data
        print("测试 encrypt_data:")
        username = "elonmusk"
        user_id = "44196397"
        
        start_time = time.time()
        for i in range(10):
            encrypted = api.encrypt_data(username, user_id)
        end_time = time.time()
        print(f"加密10次耗时: {end_time - start_time}秒")
        print(f"用户名: {username}")
        print(f"用户ID: {user_id}")
        print(f"加密结果: {encrypted}")
        print()
        
        # 测试其他函数
        print("测试 check_browser_environment:")
        browser_env = api.check_browser_environment()
        print(f"浏览器环境检查: {browser_env}")
        print()
        
        print("测试 generate_fake_hash:")
        test_input = "test_input_string"
        fake_hash = api.generate_fake_hash(test_input)
        print(f"输入: {test_input}")
        print(f"假哈希: {fake_hash}")
        
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # 运行测试
    test_wasm_functions()


# 使用示例代码
"""
使用示例：

# 1. 基本使用
from wasm_caller import PumpPillAPI

api = PumpPillAPI("pkg")  # pkg目录路径
encrypted_data = api.encrypt_data("username", "user_id")
print(f"加密结果: {encrypted_data}")

# 2. 批量处理
users = [
    ("user1", "12345"),
    ("user2", "67890"),
    ("user3", "11111")
]

for username, user_id in users:
    try:
        result = api.encrypt_data(username, user_id)
        print(f"{username} -> {result}")
    except Exception as e:
        print(f"处理 {username} 失败: {e}")

# 3. 错误处理
try:
    result = api.encrypt_data("test", "123")
    print(f"成功: {result}")
except RuntimeError as e:
    print(f"WASM调用错误: {e}")
except FileNotFoundError as e:
    print(f"文件未找到: {e}")
"""
