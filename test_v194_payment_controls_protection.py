{% extends "base.html" %}
{% from "_macros.html" import empty_state %}
{% block title %}Моя библиотека — Вокслира{% endblock %}
{% block body_class %}library-page illustrated-miniapp{% endblock %}
{% block content %}
<!-- legacy visual asset: /static/img/miniapp/voxlyra-mark.webp -->
<section class="hero compact library-hero illustrated-hero library-illustrated-hero">
  <div class="illustrated-hero-backdrop" aria-hidden="true"></div>
  <div class="profile-medallion frame-standard" id="libraryProfileFrame" aria-label="Профиль пользователя">
    <img src="/static/img/miniapp/icons/profile.webp?v={{ asset_version }}" alt="Профиль" id="libraryProfileIcon">
    <span id="libraryProfileInitial" hidden>В</span>
  </div>
  <div class="hero-copy library-hero-copy">
    <span class="eyebrow">Личная полка</span>
    <h1>Моё <span class="premium-profile-badge" id="libraryPremiumBadge" hidden>Premium</span></h1>
    <strong class="library-profile-name" id="libraryProfileName" hidden></strong>
    <p>Продолжение чтения, любимые книги, аудио и покупки.</p>
  </div>
</section>
<a class="author-studio-entry premium-entry" href="/premium"><img src="/static/img/miniapp/icons/premium.webp?v=1.11.0" alt="" aria-hidden="true"><div><b>VoxLyra Premium</b><small>Комфорт, оформление и приоритетная озвучка</small></div><i>›</i></a>
<a class="author-studio-entry" id="authorStudioEntry" href="/author" hidden><img src="/static/img/miniapp/icons/author.webp?v=1.10.4-icons2" alt="" aria-hidden="true"><div><b>Кабинет автора</b><small>Книги, главы, импорт и публикация</small></div><i>›</i></a>
<a class="author-studio-entry control-center-entry" id="controlCenterEntry" href="/control" hidden><img src="/static/img/miniapp/icons/control.webp?v=1.10.4-icons2" alt="" aria-hidden="true"><div><b>Центр управления</b><small id="controlCenterHint">Модерация и рабочие очереди</small></div><i>›</i></a>
<section class="achievement-panel library-achievements" id="libraryAchievementPanel" hidden>
  <div class="section-title split-title slim"><div><span class="eyebrow">Личный прогресс</span><h2>Мои достижения</h2><p>Награды за чтение, коллекцию, отзывы и авторскую работу.</p></div><button class="secondary compact-button" id="toggleAllAchievements" type="button">Показать все</button></div>
  <div class="achievement-grid" id="libraryAchievements"></div>
</section>
<section class="library-panel" id="libraryPage">
  <div class="library-tabs"><button class="active" data-library-tab="continue">Продолжить</button><button data-library-tab="saved">Сохранённое</button><button data-library-tab="purchases">Покупки</button></div>
  <div id="libraryContent">{{ empty_state('chapter-loading', 'Собираем вашу полку', 'Это займёт всего мгновение.', extra_class='loading-card') }}</div>
</section>
{% endblock %}
