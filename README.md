# miwifi-ssh-unlock

**Redmi AX1800/CR660X 路由器(MiWiFi 官方固件)一键开启 SSH 脚本。**

**测试固件版本：1.0.93，cr660x的运营商固件请先用恢复工具刷为小米官方固件**

纯标准库实现，**零依赖**，输入路由器 IP 和管理密码即可完成，自带连接验证和本机主机指纹自动刷新。

> ⚠️ **免责声明**：本工具仅供学习研究以及管理**自己拥有的**路由器使用。请遵守当地法律法规与厂商服务条款，勿用于他人设备。

---

## 功能特性

- ✅ 复刻网页端登录算法，自动获取会话 `stok`（加密参数从登录页动态解析，不写死，适配不同固件版本）
- ✅ 通过命令注入通道分块执行开启命令，**每块带回显标记校验**，任一步失败都会明确报错
- ✅ 开启 dropbear SSH 并设置 root 密码（默认与管理密码相同）
- ✅ 写入 `nvram` 持久化标志 + 注册开机自启
- ✅ 真实探测 22 端口 banner 验证开启结果
- ✅ **自动刷新本机 `known_hosts` 主机指纹**，重跑脚本后直接连接，不再报 `HOST IDENTIFICATION HAS CHANGED`
- ✅ 纯标准库，Python 3.6+ 开箱即用，无需 `pip install`

## 测试环境

| 项目 | 值 |
|---|---|
| 设备 | Redmi AX1800（型号 RA71） |
| 固件 | MiWiFi 稳定版 1.0.93 |
| 系统 | Linux XiaoQiang 4.4.198 (mips) |
| Python | 3.6+（实测 3.14） |
| 平台 | Windows / Linux / macOS |

同代采用 MiWiFi 网页后台（`/cgi-bin/luci/;stok=...` 结构）的小米/Redmi 路由器理论上均可，脚本会在运行时检测注入通道是否存在，不支持时会明确提示。

## 使用方法

### 交互式（推荐）

```bash
python miwifi_ssh_unlock.py
```

```
=== 小米/Redmi 路由器一键开启 SSH (root) ===
路由器 IP [192.168.31.1]: 192.168.31.1
路由器管理密码: ********
... 登录 192.168.31.1 ...
[OK] 登录成功, stok=6721be71...
[OK] 写入 nvram 标志 ssh_en/uart_en/boot_wait 并 commit
[OK] 备份 dropbear 启动脚本 -> dropbear_backup
[OK] 移除 dropbear 启动脚本里的 SSH 限制
[OK] 设置 dropbear channel=debug
[OK] 设置 root 密码
[OK] 注册开机自启并立即启动 dropbear
[OK] nvram ssh_en=1 已生效
[OK] SSH 已开启: SSH-2.0-dropbear
[OK] 本机 known_hosts 主机指纹已更新为本次新密钥, 可直接连接
```

### 免交互

```bash
python miwifi_ssh_unlock.py 192.168.31.1 '你的管理密码'
```

### 连接

```bash
ssh -o HostKeyAlgorithms=+ssh-rsa root@192.168.31.1
# 用户名: root   密码: 与管理密码相同
```

> 必须加 `-o HostKeyAlgorithms=+ssh-rsa`：老版 dropbear 只提供 SHA-1 的 `ssh-rsa` 主机密钥，新版 OpenSSH 默认不接受。

如果管理密码包含特殊字符（脚本白名单为 `字母 数字 . _ -`），脚本会提示单独设置一个 SSH root 密码。

## 工作原理

1. **登录取 stok**
   - 抓取 `/cgi-bin/luci/web` 登录页，动态解析 `Encrypt.key` 与 `nonceCreat.deviceId`
   - `nonce = 0_<deviceId>_<unix时间>_<随机数>`
   - `password = SHA1(nonce + SHA1(明文密码 + key))`
   - `POST /cgi-bin/luci/api/xqsystem/login`（`username=admin&logtype=2`），返回的 `url`/`token` 中携带 `stok`

2. **命令注入**
   - 接口：`GET /cgi-bin/luci/;stok=<stok>/api/xqdatacenter/request?payload={"api":629,"appid":"<命令>"}`
   - `appid` 拼进 shell 执行（即 CVE-2023-26315 同类通道）
   - 两个实测约束（脚本已内置处理）：
     - **payload 长度上限约 200+ 字符**，超长会被截断（截断点落在引号里会整行语法错误），所以命令必须**分块执行**
     - 输出只回显**第一行**，因此每块命令以 `; echo 标记; ...` 开头，用标记做成功校验
     - 单引号原样保留，双引号会被转义破坏 → 全程只用单引号

3. **开启 SSH**
   - `nvram set ssh_en=1` 等标志 + `nvram commit`
   - 备份并修补 `/etc/init.d/dropbear`（移除 release 守卫、`channel=debug`）
   - `({ echo 密码; echo 密码; } | passwd root)` 设置 root 密码
   - `dropbear enable + start`，并追加到 `/etc/sysupgrade.conf` 保留修改

4. **验证与指纹**
   - 探测 22 端口读取 `SSH-2.0-dropbear` banner
   - `ssh-keygen -R <ip>` 清旧指纹 → `ssh-keyscan -t rsa` 抓新密钥 → 写入 `~/.ssh/known_hosts`

## 常见问题（FAQ）

**Q: 重启后 SSH 又没了？**
该固件开机可能重建 overlay / nvram（`nvram_corrupt=1` 时尤其明显），导致上次修改丢失。**重跑一次脚本即可**，几秒钟完成。

**Q: 连接报 `REMOTE HOST IDENTIFICATION HAS CHANGED`？**
每次重新开启 SSH，dropbear 都会生成新主机密钥（旧密钥存在 overlay 里，随重启丢失）。脚本每次运行都会自动刷新 `known_hosts`；如果手动处理：

```bash
ssh-keygen -R 192.168.31.1
# 或跳过确认
ssh -o HostKeyAlgorithms=+ssh-rsa -o StrictHostKeyChecking=accept-new root@192.168.31.1
```

**Q: 报 `no matching host key type found`？**
加 `-o HostKeyAlgorithms=+ssh-rsa`。

**Q: 提示 `该固件没有 xqdatacenter/request 注入通道`？**
该固件版本未包含此接口（`No page is registered`），本脚本不适用，请勿强行尝试其他破坏性方法。

**Q: 登录失败 `code=403`？**
密码错误次数过多被临时锁定，等待几分钟再试。

**Q: 报 `stok 已失效`？**
stok 会过期，直接重新运行脚本即可（脚本会重新登录）。

**Q: 22 端口一直等不到 banner？**
看前面是否有 `[X ]` 步骤报错；也确认路由器防火墙未拦截本机访问。

## 安全建议

- 仅在**自己拥有**的路由器上使用
- SSH 仅用于内网管理，**不要在防火墙上放行 22 端口到外网**
- 建议登录后执行 `passwd root` 设置独立于管理密码的强密码
- root 权限极高，操作前确认命令

## 文件

```
miwifi_ssh_unlock.py   # 主脚本（约 330 行，纯标准库）
README.md              # 本说明
```

## 参考

- [KaMyKaSii/Unlock-SSH-on-Xiaomi-and-Redmi-WiFi-6-routers](https://github.com/KaMyKaSii/Unlock-SSH-on-Xiaomi-and-Redmi-WiFi-6-routers)
- [whypuss/xiaomi-ssh-unlock](https://github.com/whypuss/xiaomi-ssh-unlock)（CVE-2023-26315）
- [CVE-2023-26315](https://nvd.nist.gov/vuln/detail/CVE-2023-26315)

## License

MIT
