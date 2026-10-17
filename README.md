# miwifi-ssh-unlock

**Redmi AX1800/CR660X 路由器(MiWiFi 官方固件)一键开启 SSH 脚本。**

## 测试环境

| 项目 | 值 |
|---|---|
| 设备 | Redmi AX1800（型号 RA71） |
| 固件 | MiWiFi 稳定版 1.0.93 |
| 系统 | Linux XiaoQiang 4.4.198 (mips) |
| Python | 3.6+（实测 3.14） |
| 平台 | Windows / Linux / macOS |

同代采用 MiWiFi 网页后台（`/cgi-bin/luci/;stok=...` 结构）的小米/Redmi 路由器理论上均可，脚本会在运行时检测注入通道是否存在，不支持时会明确提示。

纯标准库实现，**零依赖**，输入路由器 IP 和管理密码即可完成，自带连接验证和本机主机指纹自动刷新。

> ⚠️ **免责声明**：本工具仅供学习研究以及管理**自己拥有的**路由器使用。请遵守当地法律法规与厂商服务条款，勿用于他人设备。

> 🔑 **临时 root**：本脚本开启的是**临时 root**，该固件重启后 SSH 会失效（重跑脚本即可恢复）。**固化（重启后保留）方案请自行查找解决方案**，本项目不提供、不承诺。

---

## 功能特性

- ✅ 复刻网页端登录算法，自动获取会话 `stok`（`key` / `deviceId` 均从登录页动态解析，解析失败直接报错退出）
- ✅ 通过命令注入通道分块执行开启命令，每块带回显标记校验，任一步失败都会明确报错
- ✅ 开启 dropbear SSH 并设置 root 密码（默认与管理密码相同）
- ✅ 写入 `nvram` 标志（`ssh_en` 等）
- ✅ 真实探测 22 端口 banner 验证开启结果
- ✅ 自动刷新本机 `known_hosts` 主机指纹，重跑脚本后直接连接，不再报 `HOST IDENTIFICATION HAS CHANGED`
- ✅ 纯标准库，Python 3.6+ 开箱即用，无需 `pip install`
- ✅ 网络请求自动重试 ×3 + 30s 超时，应对路由器 httpd 偶发断连、dropbear 冷启动生成密钥慢导致的失败



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
[OK] 启动 dropbear
[OK] nvram ssh_en=1 已生效
[OK] SSH 已开启: SSH-2.0-dropbear
[OK] 本机 known_hosts 主机指纹已更新为本次新密钥, 可直接连接
提示: 本脚本开启的是【临时 root】, 该固件重启后会失效, 重跑一次即可恢复。
      如需固化(重启后保留), 请自行查找解决方案, 本项目不提供。
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


## 常见问题（FAQ）

**Q: 重启后 SSH 又没了？**
本脚本提供的是**临时 root**：该固件开机可能重建 overlay / nvram（`nvram_corrupt=1` 时尤其明显），重启后修改丢失属于预期行为。**重跑一次脚本即可恢复**（几秒钟）。如需固化（重启后保留），请自行检索社区的固化方案，本项目不提供。

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

**Q: 脚本偶尔报 `网络错误`（timed out / Remote end closed）？**
两个常见原因：路由器 web 服务偶发繁忙/断连；或 dropbear **首次启动时现场生成 RSA 主机密钥**（MIPS 小 CPU 上可能要十几秒）导致请求超时——此时命令往往已经在路由器上执行成功，**重跑一次通常就正常**。脚本已内置 3 次自动重试 + 30s 超时，可覆盖绝大多数情况。

## 参考

- [KaMyKaSii/Unlock-SSH-on-Xiaomi-and-Redmi-WiFi-6-routers](https://github.com/KaMyKaSii/Unlock-SSH-on-Xiaomi-and-Redmi-WiFi-6-routers)
- [whypuss/xiaomi-ssh-unlock](https://github.com/whypuss/xiaomi-ssh-unlock)（CVE-2023-26315）
- [CVE-2023-26315](https://nvd.nist.gov/vuln/detail/CVE-2023-26315)

## License

MIT
