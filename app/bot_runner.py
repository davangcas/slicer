import telebot
import os
import logging

# Configurar el sistema de logging para que los logs sean visibles en Docker
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    logging.error("TELEGRAM_BOT_TOKEN no encontrado en las variables de entorno.")
else:
    try:
        # Inicializar el bot
        bot = telebot.TeleBot(TOKEN)
        
        # Handler que captura y registra todos los mensajes de texto
        @bot.message_handler(content_types=['text'])
        def handle_all_messages(message):
            logging.info("-" * 40)
            logging.info("📩 MENSAJE RECIBIDO DE TELEGRAM")
            logging.info(f"Chat ID: {message.chat.id}")
            # Usamos getattr para manejar la posibilidad de que first_name no exista en todos los tipos de chat
            username = getattr(message.from_user, 'first_name', 'N/A')
            logging.info(f"Nombre de Usuario: {username}") 
            logging.info(f"Texto del Mensaje: {message.text}")
            logging.info("-" * 40)

            if message.text and message.text.lower() == '/start':
                bot.reply_to(message, "¡Hola! La conexión del bot a Telegram es exitosa y ahora registra todos tus mensajes.")
            else:
                # Respondemos al usuario para confirmación de recepción en el chat de Telegram
                try:
                    bot.send_message(message.chat.id, f"Mensaje recibido y registrado por el bot.")
                except Exception as e_reply:
                    logging.error(f"No se pudo responder al mensaje del usuario. Error: {e_reply}")

        # Iniciar el bucle de polling para mantener la escucha activa
        logging.info("Iniciando Polling del Bot de Telegram...")
        bot.infinity_polling()
        
    except Exception as e:
        logging.error(f"Error al inicializar o ejecutar el bot: {e}")
        bot.infinity_polling()
        
    except Exception as e:
        print(f"Error al inicializar o ejecutar el bot: {e}")
        bot.infinity_polling()
        
    except Exception as e:
        print(f"Error al inicializar o ejecutar el bot: {e}")