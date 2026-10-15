# daikin_ir

客廳 Daikin 冷氣（ar_smart_ir 碼組 1101 的 Daikin 280-bit 協定）透過 Zigbee2MQTT 的 ZS06 紅外線發射器控制。

- 依狀態即時產生完整的 3 個 frame，不查碼表；frame 1/3 與 1101.json 全部 520 組錄碼逐 byte 相同
  （heat/lowMedium/28 是碼組本身錄錯，測試裡列為已知例外）
- 1 秒內的連續變更合併成一筆、兩筆至少間隔 3 秒，避免 ZS06 傳輸重疊而當機
- Tuya FastLZ 壓縮後每筆約 222 bytes（zosung 5 段）

搭配 `ha-config/zigbee2mqtt/external_converters/zs06_queued.js`（z2m 端每台裝置的送碼佇列）。

## 設定

```yaml
climate:
  - platform: daikin_ir
    name: 客廳冷氣
    unique_id: livingroom_daikin_ir
    topic: zigbee2mqtt/IRemote/set
    temperature_sensor: sensor.0xa4c13803518b7a02_temperature
    humidity_sensor: sensor.0xa4c13803518b7a02_humidity
    # debounce: 1.0      # 秒
    # min_interval: 3.0  # 秒
```

## 測試

`tests/test_protocol.py` 需要旁邊的 `../ar_smart_ir` 碼組。本機沒有 pytest 時可直接執行每個 `test_*` 函式：

```sh
python3 -I -m pytest tests
```

## 部署

```sh
tar --exclude=__pycache__ -czf - -C custom_components daikin_ir \
  | ssh home-ha 'tar -xzf - -C /homeassistant/custom_components'
```

改 `climate.py` / `protocol.py` 後需重啟 HA core。

## 授權

MIT，見 [LICENSE](LICENSE)。

`custom_components/daikin_ir/tuya_codec.py` 原封不動取自
[ar_smart_ir](https://github.com/marsh4200/ar_smart_ir)，
MIT License，Copyright (c) 2026 marsh4200 (ARSmartHome)，版權聲明保留在檔頭。
