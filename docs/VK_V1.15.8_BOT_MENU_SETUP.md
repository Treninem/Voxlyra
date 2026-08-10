# VK bot menu: required community setting

VoxLyra receives VK messages through Community Long Poll and sends an inline
native Mini App menu. VK returns API error `912` when the community setting
**Chat bot feature / Возможности ботов** is disabled.

Enable it in the VoxLyra community:

1. **Управление → Сообщения → Настройки для бота**.
2. Turn on **Возможности ботов**.
3. Keep community messages, Long Poll API and `message_new` enabled.
4. Send the community a new message. A container restart is not required for
   the VK setting itself.

Version 1.15.8 also retries error 912 without a keyboard. This means users get
an application link instead of silence while the setting is off. VK itself
will not accept the full keyboard until the administrator enables the feature.
