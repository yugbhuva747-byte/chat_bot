from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('api/chat/', views.chat, name='chat'),
    path('api/sessions/', views.get_sessions, name='get_sessions'),
    path('api/sessions/new/', views.new_session, name='new_session'),
    path('api/sessions/<str:session_id>/', views.get_session, name='get_session'),
    path('api/sessions/<str:session_id>/delete/', views.delete_session, name='delete_session'),
    path('api/audio/', views.whisper_audio, name='whisper_audio'),
    path('api/document/', views.document_upload, name='document_upload'),
    path('api/generate-prompt/', views.generate_prompt, name='generate_prompt'),
]
