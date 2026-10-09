# ❖ 蓝牙操控

- **页面 id**：`ble`
- **路由**：`/ble`
- **分组**：设备
- **一句说明**：本机蓝牙多源枚举 + 真实 BLE 扫描 + 授权闸门写操作 + 追加式审计

## 这个页面用到的接口

- `/api/ble/status`
- `/api/ble/scan`
- `/api/ble/ops`
- `/api/ble/report`

## 绑定的资源（双向的一半）

- 蓝牙适配器
- BLE 设备
- Grant 授权
- ble_ops 审计

## 反向入口（从资源侧回到本页）

- 设备行→/api/ble/scan
- 操作→/api/ble/ops
