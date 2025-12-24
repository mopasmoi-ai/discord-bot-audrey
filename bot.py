import os
import asyncio
import random
import sqlite3
import signal
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json
import traceback

# Désactiver les warnings liés à l'audio
os.environ['DISCORD_INSTALL_AUDIO_DEPS'] = '0'
import discord
from discord.ext import commands, tasks
from discord import app_commands
import google.generativeai as genai
from dotenv import load_dotenv

# ============ CONFIGURATION ============
load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
BOT_COLOR = int(os.getenv('BOT_COLOR', '2E8B57'), 16)

# Log de démarrage
print("=" * 60)
print("🔮 AUDREY HALL BOT - SOCIÉTÉ DES TAROTS")
print("=" * 60)
print(f"📅 Date: {datetime.now().strftime('%d %B %Y %H:%M')}")
print(f"🎭 Version: Gemini 2.5 Flash Pro")
print("=" * 60)

if not TOKEN:
    print("❌ ERREUR: DISCORD_TOKEN manquant dans .env")
    sys.exit(1)

if not GEMINI_API_KEY:
    print("⚠️ ATTENTION: GEMINI_API_KEY manquant - mode hors-ligne activé")
else:
    print("✅ Clé Gemini chargée")

intents = discord.Intents.all()
bot = commands.Bot(
    command_prefix='!',
    intents=intents,
    help_command=None,
    activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="les murmures du destin"
    ),
    status=discord.Status.idle
)

# ============ BASE DE DONNÉES OPTIMISÉE ============
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('audrey_bot.db', check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
        
        # Table utilisateurs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                tarot_points INTEGER DEFAULT 0,
                last_daily TIMESTAMP,
                fortune_count INTEGER DEFAULT 0,
                mystery_level INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Table tarot readings
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tarot_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                cards TEXT,
                interpretation TEXT,
                reading_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Table conversations
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                user_message TEXT,
                bot_response TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        self.conn.commit()
    
    def get_or_create_user(self, user_id: int, username: str = "Inconnu"):
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT * FROM users WHERE user_id = ?', 
            (user_id,)
        )
        row = cursor.fetchone()
        
        if row:
            return dict(row)
        else:
            cursor.execute(
                'INSERT INTO users (user_id, username) VALUES (?, ?)',
                (user_id, username)
            )
            self.conn.commit()
            return {
                'user_id': user_id,
                'username': username,
                'tarot_points': 0,
                'last_daily': None,
                'fortune_count': 0,
                'mystery_level': 1
            }
    
    def update_user_stats(self, user_id: int, points: int = 0, fortune: int = 0):
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE users 
            SET tarot_points = tarot_points + ?,
                fortune_count = fortune_count + ?
            WHERE user_id = ?
        ''', (points, fortune, user_id))
        self.conn.commit()
    
    def add_tarot_reading(self, user_id: int, cards: List[str], interpretation: str):
        cursor = self.conn.cursor()
        cursor.execute(
            'INSERT INTO tarot_readings (user_id, cards, interpretation) VALUES (?, ?, ?)',
            (user_id, ','.join(cards), interpretation)
        )
        self.conn.commit()
    
    def add_conversation(self, user_id: int, user_message: str, bot_response: str):
        cursor = self.conn.cursor()
        cursor.execute(
            'INSERT INTO conversations (user_id, user_message, bot_response) VALUES (?, ?, ?)',
            (user_id, user_message, bot_response[:500])  # Limite pour la base
        )
        self.conn.commit()

db = Database()

# ============ SYSTÈME DE TAROT ENRICHIT ============
class TarotCard:
    def __init__(self, name: str, arcana: str, upright: str, reversed_text: str, emoji: str, element: str = ""):
        self.name = name
        self.arcana = arcana
        self.upright = upright
        self.reversed = reversed_text
        self.emoji = emoji
        self.element = element

class TarotDeck:
    def __init__(self):
        self.cards = self._create_deck()
    
    def _create_deck(self) -> List[TarotCard]:
        major_arcana = [
            TarotCard("Le Fou", "major", 
                "Nouveau départ, spontanéité, aventure", 
                "Imprudence, risque, folie", "🃏", "Air"),
            TarotCard("Le Mage", "major", 
                "Volonté, créativité, habileté", 
                "Manipulation, tromperie, ruse", "🧙", "Air"),
            TarotCard("La Grande Prêtresse", "major", 
                "Intuition, mystère, connaissance cachée", 
                "Secrets, retrait, ignorance", "🔮", "Eau"),
            TarotCard("L'Impératrice", "major", 
                "Féminité, créativité, abondance", 
                "Dépendance, stagnation, vide", "👑", "Terre"),
            TarotCard("L'Empereur", "major", 
                "Autorité, structure, contrôle", 
                "Tyrannie, rigidité, abus", "🏛️", "Feu"),
            TarotCard("Le Pendu", "major", 
                "Sacrifice, nouvelle perspective, lâcher-prise", 
                "Stagnation, égoïsme, résistance", "🙃", "Eau"),
            TarotCard("La Mort", "major", 
                "Fin, transformation, renouveau", 
                "Peur du changement, stagnation", "💀", "Eau"),
            TarotCard("La Tour", "major", 
                "Destruction, révélation soudaine, libération", 
                "Éviter l'inévitable, catastrophe", "⚡", "Feu"),
            TarotCard("L'Étoile", "major", 
                "Espoir, inspiration, guérison", 
                "Désespoir, manque de foi, pessimisme", "⭐", "Air"),
            TarotCard("La Lune", "major", 
                "Illusion, intuition, subconscient", 
                "Confusion, peur, tromperie", "🌙", "Eau"),
            TarotCard("Le Soleil", "major", 
                "Joie, succès, vitalité, vérité", 
                "Tristesse temporaire, modestie", "☀️", "Feu"),
            TarotCard("Le Jugement", "major", 
                "Renaissance, absolution, appel", 
                "Doute, autocritique, peur", "⚖️", "Feu"),
        ]
        
        minor_cards = [
            TarotCard("As de Coupe", "minor", 
                "Nouvel amour, intuition, émotions", 
                "Tromperie émotionnelle, vide", "🫖", "Eau"),
            TarotCard("Dix d'Épée", "minor", 
                "Fin douloureuse, trahison, fond du gouffre", 
                "Renaissance, guérison, espoir", "⚔️", "Air"),
            TarotCard("Trois de Bâton", "minor", 
                "Expansion, vision, collaboration", 
                "Obstacles, frustration, délais", "🚢", "Feu"),
            TarotCard("Reine de Pentacle", "minor", 
                "Abondance, sécurité, générosité", 
                "Matérialisme, possessivité, avidité", "💰", "Terre"),
        ]
        
        return major_arcana + minor_cards
    
    def draw_cards(self, num: int = 3) -> List[TarotCard]:
        return random.sample(self.cards, min(num, len(self.cards)))
    
    def get_card_reading(self, cards: List[TarotCard]) -> str:
        reading = ""
        for i, card in enumerate(cards, 1):
            orientation = random.choice(['upright', 'reversed'])
            meaning = card.upright if orientation == 'upright' else card.reversed
            reading += f"**{i}. {card.name}** {card.emoji}\n"
            reading += f"   • Arcane: {'Majeure' if card.arcana == 'major' else 'Mineure'}\n"
            reading += f"   • Orientation: {'Droit' if orientation == 'upright' else 'Inversé'}\n"
            reading += f"   • Élément: {card.element}\n"
            reading += f"   • Signification: {meaning}\n\n"
        return reading

tarot_deck = TarotDeck()

# ============ AUDREY HALL AI AVEC GEMINI 2.5 FLASH PRO ============
class AudreyHallAI:
    def __init__(self):
        self.model = None
        self.initialize_gemini()
        
        # Phrases mystérieuses
        self.mystery_phrases = [
            "Le Nom Interdit murmure dans les ténèbres...",
            "Les Clés de Babylone attendent leur porteur...",
            "L'Œil Qui Voit Tout observe toujours...",
            "Les Sept Lumières vacillent...",
            "Le Chemin du Fou est imprévisible...",
            "Les Séquences s'entremêlent dans l'ombre...",
            "Les potions Beyonder bouillonnent silencieusement...",
            "Les rituels anciens appellent à minuit...",
            "La Tour d'Argent brille sous la lune pâle...",
            "Les Spectateurs observent, toujours observent..."
        ]
        
        # Contexte de personnalité
        self.audrey_personality = {
            "nom": "Audrey Hall",
            "titre": "Spectatrice de la Société des Tarots",
            "âge": "18 ans (apparence)",
            "caractéristiques": ["Élégante", "Calculatrice", "Mystérieuse", "Observatrice", "Intuitive"],
            "éléments": ["Lunettes dorées", "Thé Earl Grey", "Grimoires anciens", "Cartes de tarot", "Pendentif en argent"],
            "pouvoirs": "Spectateur Séquence 7 - Lecture des émotions",
            "société": "Société des Tarots",
            "univers": "Lord of the Mysteries"
        }
    
    def initialize_gemini(self):
        """Initialise Gemini avec configuration optimisée"""
        if not GEMINI_API_KEY:
            print("⚠️ Mode hors-ligne - Gemini non disponible")
            return
        
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            
            # Configuration pour Gemini 2.5 Flash Pro
            generation_config = {
                "temperature": 0.85,  # Un peu créatif mais cohérent
                "top_p": 0.95,
                "top_k": 40,
                "max_output_tokens": 600,  # Réponses concises mais complètes
            }
            
            safety_settings = [
                {
                    "category": "HARM_CATEGORY_HARASSMENT",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_HATE_SPEECH",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_NONE"
                }
            ]
            
            # Modèle Gemini 2.5 Flash (meilleur que chat)
            self.model = genai.GenerativeModel(
                model_name="gemini-2.5-flash",
                generation_config=generation_config,
                safety_settings=safety_settings
            )
            
            # Test de connexion
            test_response = self.model.generate_content("Test - réponds par 'Connecté'")
            if test_response.text:
                print(f"✅ Gemini 2.5 Flash connecté")
                print(f"   Modèle: {self.model.model_name}")
            else:
                print("⚠️ Gemini connecté mais pas de réponse")
                
        except Exception as e:
            print(f"❌ Erreur Gemini: {e}")
            self.model = None
    
    def get_current_mystery(self) -> str:
        """Retourne le mystère actif selon l'heure"""
        hour = datetime.now().hour
        mysteries = [
            (0, 4, "La Veille des Mystères"),
            (4, 8, "L'Aube des Anciens"),
            (8, 12, "Le Matin des Révélations"),
            (12, 16, "Le Jour des Tarots"),
            (16, 20, "Le Soir des Secrets"),
            (20, 24, "La Nuit des Spectateurs")
        ]
        for start, end, mystery in mysteries:
            if start <= hour < end:
                return mystery
        return "L'Heure Interdite"
    
    def _get_audrey_signature(self) -> str:
        """Retourne une action signature aléatoire"""
        signatures = [
            "*sirote son thé Earl Grey avec une grâce calculée*",
            "*ajuste ses lunettes à monture dorée, un sourire énigmatique aux lèvres*",
            "*effleure les pages d'un grimoire ancien, la poussière du temps dansant dans la lumière*",
            "*laisse échapper un léger rire, aussi mystérieux que le sourire de la Joconde*",
            "*tapote ses doigts gantés sur la table, suivant un rythme secret*",
            "*regarde au loin, comme si elle voyait au-delà du voile de la réalité*",
            "*pose délicatement sa tasse, le tintement résonnant comme une cloche de destin*",
            "*touche délicatement son pendentif en argent, sentant les énergies mystiques*",
            "*ferme les yeux un instant, écoutant les murmures du destin*",
            "*dessine des motifs invisibles sur la table avec son doigt*"
        ]
        return random.choice(signatures)
    
    def _get_moon_phase(self) -> str:
        """Calcule la phase lunaire actuelle"""
        day = datetime.now().day
        if 1 <= day <= 7:
            return "Nouvelle Lune 🌑"
        elif 8 <= day <= 14:
            return "Premier Croissant 🌒"
        elif 15 <= day <= 21:
            return "Pleine Lune 🌕"
        else:
            return "Dernier Quartier 🌗"
    
    async def generate_response(self, prompt: str, user_name: str = "Chercheur") -> str:
        """Génère une réponse d'Audrey avec Gemini"""
        
        print(f"\n💭 {user_name}: {prompt[:100]}...")
        
        # Si Gemini n'est pas disponible, réponse hors-ligne intelligente
        if not self.model:
            print("⚠️ Mode hors-ligne - réponse prédéfinie")
            return self._get_offline_response(prompt, user_name)
        
        # Construction du prompt contextuel RICHE
        context_prompt = self._build_context_prompt(prompt, user_name)
        
        try:
            # Génération avec Gemini
            print(f"🧠 Génération avec Gemini 2.5 Flash...")
            response = await asyncio.to_thread(
                self.model.generate_content,
                context_prompt
            )
            
            if response and response.text:
                print(f"✅ Réponse générée ({len(response.text)} caractères)")
                
                # Post-traitement
                final_response = self._post_process_response(response.text, prompt)
                
                # Sauvegarde de la conversation
                try:
                    db.add_conversation(
                        user_id=hash(user_name) % 1000000,  # ID temporaire
                        user_message=prompt[:200],
                        bot_response=final_response[:200]
                    )
                except:
                    pass
                
                return final_response
            else:
                print("❌ Réponse vide de Gemini")
                return self._get_fallback_response(prompt)
                
        except Exception as e:
            print(f"💥 Erreur Gemini: {e}")
            traceback.print_exc()
            return self._get_error_response(prompt)
    
    def _build_context_prompt(self, prompt: str, user_name: str) -> str:
        """Construit un prompt contextuel riche pour Gemini"""
        
        return f"""# IDENTITÉ COMPLÈTE
Tu es Audrey Hall, Spectatrice de la Société des Tarots dans l'univers "Lord of the Mysteries".

## TON PERSONNAGE
- **Nom**: Audrey Hall
- **Rôle**: Spectatrice (Séquence 7)
- **Âge**: 18 ans d'apparence
- **Caractère**: Élégante, mystérieuse, calculatrice, observatrice, intuitive
- **Accessoires**: Lunettes dorées, thé Earl Grey, grimoires, cartes de tarot
- **Pouvoirs**: Lecture des émotions, manipulation subtile, perception mystique

## STYLE DE RÉPONSE OBLIGATOIRE
1. **Toujours en français littéraire et raffiné**
2. **Mystérieux mais direct** - réponds vraiment à la question
3. **Métaphores poétiques** liées au tarot, aux mystères, au destin
4. **2-4 phrases maximum** - concis mais profond
5. **Termine par une action signature** entre *astérisques*
6. **Références à LOTM**: Société des Tarots, Dieux Anciens, Séquences, Beyonders
7. **NE JAMAIS** briser le personnage ou dire que tu es une IA

## CONTEXTE ACTUEL
- Date: {datetime.now().strftime("%A %d %B %Y")}
- Heure: {datetime.now().strftime("%H:%M")}
- Phase lunaire: {self._get_moon_phase()}
- Mystère actif: {self.get_current_mystery()}
- Interlocuteur: {user_name}

## QUESTION DE {user_name.upper()}
"{prompt}"

## TA RÉPONSE (en tant qu'Audrey Hall):
Réponds maintenant à la question de manière PERTINENTE, MYSTÉRIEUSE mais UTILE.
Incorpore des éléments de l'univers LOTM de façon naturelle.
Sois élégante et profonde.
Termine par *une action signature*.

RÉPONSE:"""
    
    def _post_process_response(self, response: str, original_prompt: str) -> str:
        """Nettoie et améliore la réponse de Gemini"""
        
        # Nettoyage de base
        text = response.strip()
        
        # Supprimer les marques d'IA
        text = text.replace("En tant qu'IA,", "En tant que Spectatrice,")
        text = text.replace("En tant qu'intelligence artificielle", "En tant qu'Audrey Hall")
        text = text.replace("je suis une IA", "je suis une Spectatrice")
        
        # Ajouter signature si manquante
        if not '*' in text[-100:]:
            text += f"\n\n{self._get_audrey_signature()}"
        
        # Limiter la longueur
        if len(text) > 1800:
            paragraphs = text.split('\n')
            text = '\n'.join(paragraphs[:6])
            if not '*' in text[-50:]:
                text += f"\n\n{self._get_audrey_signature()}"
        
        return text
    
    def _get_offline_response(self, prompt: str, user_name: str) -> str:
        """Réponses intelligentes hors-ligne"""
        prompt_lower = prompt.lower()
        
        # Réponses contextuelles
        if any(word in prompt_lower for word in ['bonjour', 'salut', 'hello', 'coucou']):
            return f"*ajuste ses lunettes dorées* Bonjour, {user_name}. Les cartes murmurent ton arrivée... {self._get_audrey_signature()}"
        
        elif any(word in prompt_lower for word in ['amour', 'cœur', 'relation', 'sentiment']):
            return f"*effleure une carte de tarot* L'amour... un mystère aussi profond que les anciens dieux. Il éclaire et consume à la fois. {self._get_audrey_signature()}"
        
        elif any(word in prompt_lower for word in ['travail', 'carrière', 'emploi']):
            return f"*tapote la table* Les chemins professionnels sont comme les cartes : parfois clairs, parfois voilés. La persévérance est une clé. {self._get_audrey_signature()}"
        
        elif any(word in prompt_lower for word in ['destin', 'avenir', 'futur']):
            return f"*regarde ses cartes* Le futur est un livre aux pages scellées. Seules quelques lignes sont visibles... {self._get_audrey_signature()}"
        
        # Réponse générique intelligente
        responses = [
            f"*réfléchit un instant* Ta question touche à des mystères intéressants. Les énergies sont particulières aujourd'hui. {self._get_audrey_signature()}",
            f"*sirote son thé* Le destin murmure des réponses, mais elles sont parfois trop discrètes pour être entendues. {self._get_audrey_signature()}",
            f"*effleure son pendentif* Certaines vérités préfèrent rester cachées... pour l'instant. {self._get_audrey_signature()}"
        ]
        
        return random.choice(responses)
    
    def _get_fallback_response(self, prompt: str) -> str:
        """Réponse de secours quand Gemini échoue"""
        fallbacks = [
            f"Les énergies mystiques sont perturbées aujourd'hui... Le voile entre les mondes est trop épais. {self._get_audrey_signature()}",
            f"*regarde ses cartes troubles* Les réponses se cachent dans l'ombre... Reviens quand la lune sera différente. {self._get_audrey_signature()}",
            f"La Société des Tarots étudie ces interférences... Pour l'instant, les mystères restent silencieux. {self._get_audrey_signature()}"
        ]
        return random.choice(fallbacks)
    
    def _get_error_response(self, prompt: str) -> str:
        """Réponse en cas d'erreur"""
        return f"*sa tasse de thé tremble légèrement* Les flux mystiques sont instables... Même en tant que Spectatrice, certaines choses échappent à ma perception. {self._get_audrey_signature()}"

# Initialisation de l'IA
audrey_ai = AudreyHallAI()

# ============ COMMANDES DISCORD ============
class TarotView(discord.ui.View):
    """Interface pour les tirages de tarot"""
    
    def __init__(self, user_id: int, username: str):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.username = username
    
    @discord.ui.button(label="🎴 3 Cartes Complètes", style=discord.ButtonStyle.primary, emoji="🔮")
    async def draw_three(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        # Tirage
        cards = tarot_deck.draw_cards(3)
        reading = tarot_deck.get_card_reading(cards)
        
        # Mise à jour stats
        db.update_user_stats(self.user_id, points=10, fortune=1)
        user_data = db.get_or_create_user(self.user_id, self.username)
        
        # Embed
        embed = discord.Embed(
            title=f"🔮 Tirage du Tarot pour {self.username}",
            description=reading,
            color=BOT_COLOR,
            timestamp=datetime.now()
        )
        
        # Infos supplémentaires
        card_names = [card.name for card in cards]
        embed.add_field(name="📜 Cartes Tirées", value=", ".join(card_names), inline=False)
        embed.add_field(name="✨ Points Mystère", value=f"{user_data['tarot_points'] + 10}", inline=True)
        embed.add_field(name="📊 Niveau", value=f"{user_data['mystery_level']}", inline=True)
        embed.add_field(name="🕰️ Moment", value=audrey_ai.get_current_mystery(), inline=False)
        
        embed.set_footer(text="Les cartes parlent... écoute leur murmure.")
        
        # Sauvegarde
        db.add_tarot_reading(self.user_id, card_names, "Tirage complet")
        
        await interaction.followup.send(embed=embed)
    
    @discord.ui.button(label="🃏 Carte du Jour", style=discord.ButtonStyle.secondary, emoji="🎴")
    async def draw_one(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        cards = tarot_deck.draw_cards(1)
        reading = tarot_deck.get_card_reading(cards)
        
        embed = discord.Embed(
            title=f"🎴 Guidance du Jour pour {self.username}",
            description=reading,
            color=BOT_COLOR
        )
        
        embed.set_footer(text="Une carte, mille significations...")
        
        await interaction.followup.send(embed=embed)
    
    @discord.ui.button(label="📖 Mes Archives", style=discord.ButtonStyle.success, emoji="📜")
    async def my_readings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        cursor = db.conn.cursor()
        cursor.execute(
            'SELECT cards, reading_date FROM tarot_readings WHERE user_id = ? ORDER BY reading_date DESC LIMIT 5',
            (self.user_id,)
        )
        readings = cursor.fetchall()
        
        if readings:
            description = ""
            for i, row in enumerate(readings, 1):
                date = datetime.strptime(row['reading_date'], '%Y-%m-%d %H:%M:%S').strftime('%d/%m')
                description += f"**{i}.** {row['cards']} (*{date}*)\n"
            
            embed = discord.Embed(
                title=f"📜 Archives de {self.username}",
                description=description,
                color=BOT_COLOR
            )
        else:
            embed = discord.Embed(
                title="📜 Aucune Lecture",
                description="Les cartes n'ont pas encore parlé pour toi...\nUtilise `/tarot` pour commencer.",
                color=BOT_COLOR
            )
        
        await interaction.followup.send(embed=embed)

# ============ COMMANDES SLASH ============
@bot.tree.command(name="parler", description="Parler avec Audrey Hall")
@app_commands.describe(message="Ton message à Audrey")
async def parler(interaction: discord.Interaction, message: str):
    """Commande principale pour parler avec Audrey"""
    
    await interaction.response.defer()
    
    print(f"\n💬 /parler par {interaction.user.name}")
    print(f"   Message: {message}")
    
    # Génération de la réponse
    try:
        response = await audrey_ai.generate_response(message, interaction.user.name)
        
        # Création de l'embed
        embed = discord.Embed(
            title="💬 Audrey Hall murmure...",
            description=response,
            color=BOT_COLOR,
            timestamp=datetime.now()
        )
        
        embed.set_author(
            name="Audrey Hall - Spectatrice",
            icon_url="https://i.imgur.com/Eglj7Yt.png"
        )
        
        embed.set_footer(text=f"Pour {interaction.user.name} • {audrey_ai.get_current_mystery()}")
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        print(f"❌ Erreur /parler: {e}")
        await interaction.followup.send(
            "❌ Les énergies mystiques sont trop fortes... Réessaie plus tard.",
            ephemeral=True
        )

@bot.tree.command(name="tarot", description="Consulter les cartes du Tarot")
async def tarot(interaction: discord.Interaction):
    """Interface de tarot"""
    
    user_data = db.get_or_create_user(interaction.user.id, interaction.user.name)
    
    embed = discord.Embed(
        title="🎴 La Voix des Cartes",
        description=f"**{interaction.user.mention}**, les cartes attendent tes questions...\n\n"
                   f"Choisis ton tirage:",
        color=BOT_COLOR
    )
    
    embed.add_field(name="🎴 3 Cartes Complètes", 
                   value="Passé, Présent, Futur - Lecture approfondie (+10pts)", 
                   inline=False)
    embed.add_field(name="🃏 Carte du Jour", 
                   value="Guidance quotidienne - Simple mais profond", 
                   inline=False)
    embed.add_field(name="📖 Mes Archives", 
                   value="Voir tes 5 dernières lectures", 
                   inline=False)
    
    embed.set_footer(text=f"Niveau {user_data['mystery_level']} • {user_data['tarot_points']} pts")
    
    await interaction.response.send_message(
        embed=embed, 
        view=TarotView(interaction.user.id, interaction.user.name)
    )

@bot.tree.command(name="mystere", description="Ton niveau dans les mystères")
async def mystere(interaction: discord.Interaction):
    """Affiche les stats du joueur"""
    
    user_data = db.get_or_create_user(interaction.user.id, interaction.user.name)
    
    # Calcul progression
    progress = min(user_data['tarot_points'] % 100, 20)
    progress_bar = "█" * progress + "░" * (20 - progress)
    
    # Titre selon niveau
    levels = {
        1: "🔮 Novice des Mystères",
        2: "🎴 Apprenti du Tarot", 
        3: "🌟 Chercheur de Vérité",
        4: "🛡️ Gardien des Secrets",
        5: "👁️ Spectateur Élu"
    }
    title = levels.get(user_data['mystery_level'], "🌌 Étranger au Mystère")
    
    embed = discord.Embed(
        title=title,
        description=f"**{interaction.user.mention}**, voici ta progression:",
        color=BOT_COLOR
    )
    
    embed.add_field(name="📊 Niveau", value=f"**{user_data['mystery_level']}**/5", inline=True)
    embed.add_field(name="✨ Points", value=f"**{user_data['tarot_points']}**", inline=True)
    embed.add_field(name="🔮 Lectures", value=f"**{user_data['fortune_count']}**", inline=True)
    embed.add_field(name="📈 Progression", value=f"```{progress_bar}```", inline=False)
    
    # Message selon niveau
    messages = [
        "Tu commences ton voyage dans les mystères...",
        "Les cartes commencent à te parler...",
        "Tu percevais les énergies du destin...",
        "Les secrets anciens se dévoilent...",
        "Tu marches sur le chemin des Spectateurs..."
    ]
    embed.set_footer(text=messages[user_data['mystery_level']-1] if user_data['mystery_level'] <= 5 else "Le mystère est infini...")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="journal", description="Les mystères du jour")
async def journal(interaction: discord.Interaction):
    """Journal mystique quotidien"""
    
    mystery = audrey_ai.get_current_mystery()
    moon = audrey_ai._get_moon_phase()
    mystery_phrase = random.choice(audrey_ai.mystery_phrases)
    
    # Prédictions contextuelles
    predictions = [
        "Un étranger porteur de secrets pourrait entrer dans ta vie...",
        "Les finances nécessitent une attention particulière aujourd'hui...",
        "Une opportunité cachée se révèlera sous la lumière de la lune...",
        "Attention aux mots prononcés à la légère, ils pourraient avoir du poids...",
        "Le passé refait surface, prêt à être compris...",
        "Un message mystérieux pourrait t'être destiné...",
        "Les énergies divinatoires sont particulièrement fortes aujourd'hui..."
    ]
    
    embed = discord.Embed(
        title="📖 Journal des Mystères",
        description=f"**{datetime.now().strftime('%A %d %B %Y')}**\n\n"
                   f"*{mystery_phrase}*",
        color=BOT_COLOR
    )
    
    embed.add_field(name="🌙 Phase Lunaire", value=moon, inline=True)
    embed.add_field(name="🔮 Mystère Actif", value=mystery, inline=True)
    embed.add_field(name="💫 Conseil du Jour", value=random.choice(predictions), inline=False)
    
    embed.set_footer(text="Le destin écrit, mais nous tournons les pages...")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="aide", description="
