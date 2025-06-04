
import fs from 'fs';
import path from 'path';

// 模拟浏览器环境
global.window = {
    location: {
        href: 'https://twitter.com',
        origin: 'https://twitter.com',
        protocol: 'https:',
        host: 'twitter.com',
        hostname: 'twitter.com',
        port: '',
        pathname: '/',
        search: '',
        hash: ''
    },
    navigator: {
        userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        language: 'en-US',
        languages: ['en-US', 'en'],
        platform: 'Win32',
        vendor: 'Google Inc.',
        appVersion: '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    },
    document: {
        createElement: () => ({}),
        getElementById: () => null,
        querySelector: () => null
    },
    localStorage: {
        getItem: () => null,
        setItem: () => {},
        removeItem: () => {},
        clear: () => {}
    },
    sessionStorage: {
        getItem: () => null,
        setItem: () => {},
        removeItem: () => {},
        clear: () => {}
    },
    fetch: () => Promise.resolve({}),
    XMLHttpRequest: class {},
    WebSocket: class {},
    crypto: {
        getRandomValues: (arr) => {
            for (let i = 0; i < arr.length; i++) {
                arr[i] = Math.floor(Math.random() * 256);
            }
            return arr;
        },
        subtle: {
            digest: () => Promise.resolve(new Uint8Array(32)),
            encrypt: () => Promise.resolve(new Uint8Array(32)),
            decrypt: () => Promise.resolve(new Uint8Array(32))
        }
    },
    btoa: (str) => Buffer.from(str).toString('base64'),
    atob: (str) => Buffer.from(str, 'base64').toString()
};

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

// 初始化WASM模块
const wasmPath = path.join('/home/yijie/proj/bc/chainfm/wasm_hack/pkg', 'PumpPillWasm_bg.wasm');
const wasmBytes = fs.readFileSync(wasmPath);
const { default: init, encrypt_data } = await import('file:///home/yijie/proj/bc/chainfm/wasm_hack/pkg/PumpPillWasm.js');
await init(wasmBytes);

// 导出函数
export function encrypt(screen_name, user_id) {
    return encrypt_data(screen_name, user_id);
}
