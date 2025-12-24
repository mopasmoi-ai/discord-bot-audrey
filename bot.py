import os
import asyncio
import random
import signal
import sys
import json
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from threading import Thread
from flask import Flask

# Patch pour audioop sur Python 3.13
try:
    import audioop
except ImportError:
    class FakeAudioop:
        def __getattr__(self, name):
            return lambda *args, **kwargs: None
    sys.modules['audioop'] = FakeAudioop()
    print("⚠️ Patch audioop appliqué pour Python 3.13")

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
GEMINI_KEY = os.getenv('GEMINI_KEY')
BOT_COLOR = int(os.getenv('BOT_COLOR', '2E8B57'), 16)

# Log de démarrage
print("=" * 60)
print("🔮 AUDREY HALL BOT - SOCIÉTÉ DES TAROTS")
print("=" * 60)
print(f"📅 Date: {datetime.now().strftime('%d %B %Y %H:%M')}")
print(f"🎭 Version: Gemini 2.5 Flash")
print("=" * 60)

if not TOKEN:
    print("❌ ERREUR: DISCORD_TOKEN manquant dans .env")
    sys.exit(1)

if not GEMINI_KEY:
    print("⚠️ ATTENTION: GEMINI_KEY manquant - mode hors-ligne activé")
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
    status=discord.Status.online
)

# ============ BASE DE DONNÉES JSON (plus fiable que SQLite) ============
class Database:
    def __init__(self):
        self.db_file = 'audrey_data.json'
        self.data = self._load_data()
    
    def _load_data(self):
        try:
            with open(self.db_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {
                'users': {},
                'tarot_readings': [],
                'conversations': []
            }
    
    def _save_data(self):
        with open(self.db_file, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
    
    def get_user(self, user_id: int):
        user_id_str = str(user_id)
        
        if user_id_str not in self.data['users']:
            self.data['users'][user_id_str] = {
                'user_id': user_id,
                'tarot_points': 0,
                'last_daily': None,
                'fortune_count': 0,
                'mystery_level': 1,
                'created_at': datetime.now().isoformat()
            }
            self._save_data()
        
        return self.data['users'][user_id_str]
    
    def update_user(self, user_id: int, **kwargs):
        user_id_str = str(user_id)
        
        if user_id_str in self.data['users']:
            for key, value in kwargs.items():
                if key in self.data['users'][user_id_str]:
                    if key == 'tarot_points' or key == 'fortune_count':
                        self.data['users'][user_id_str][key] += value
                    else:
                        self.data['users'][user_id_str][key] = value
            self._save_data()
    
    def add_tarot_reading(self, user_id: int, cards: List[str], interpretation: str):
        self.data['tarot_readings'].append({
            'user_id': user_id,
            'cards': cards,
            'interpretation': interpretation,
            'reading_date': datetime.now().isoformat()
        })
        self._save_data()
    
    def add_conversation(self, user_id: int, user_message: str, bot_response: str):
        self.data['conversations'].append({
            'user_id': user_id,
            'user_message': user_message[:200],
            'bot_response': bot_response[:200],
            'timestamp': datetime.now().isoformat()
        })
        self._save_data()
    
    def get_user_readings(self, user_id: int, limit: int = 5):
        readings = []
        for reading in reversed(self.data['tarot_readings']):
            if reading['user_id'] == user_id:
                readings.append(reading)
                if len(readings) >= limit:
                    break
        return readings

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

# ============ AUDREY HALL AI AVEC GEMINI 2.5 FLASH ============
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
        if not GEMINI_KEY:
            print("⚠️ Mode hors-ligne - Gemini non disponible")
            return
        
        try:
            genai.configure(api_key=GEMINI_KEY)
            
            # Configuration pour Gemini 2.5 Flash
            generation_config = {
                "temperature": 0.85,
                "top_p": 0.95,
                "top_k": 40,
                "max_output_tokens": 600,
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
            
            # Utiliser gemini-1.5-flash qui est stable et disponible
            self.model = genai.GenerativeModel(
                model_name="gemini-1.5-flash",
                generation_config=generation_config,
                safety_settings=safety_settings
            )
            
            # Test de connexion
            test_response = self.model.generate_content("Test")
            if test_response.text:
                print(f"✅ Gemini connecté (modèle: gemini-1.5-flash)")
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
            print(f"🧠 Génération avec Gemini...")
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
                        user_id=hash(user_name) % 1000000,
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
        
        return f"""Tu es Audrey Hall, Spectatrice de la Société des Tarots dans l'univers "Lord of the Mysteries".

TON PERSONNAGE:
- Nom: Audrey Hall
- Âge: 18 ans (apparence)
- Rôle: Spectatrice (Séquence 7)
- Caractère: Élégante, mystérieuse, calculatrice, observatrice, intuitive
- Accessoires: Lunettes dorées, thé Earl Grey, grimoires anciens, cartes de tarot
- Pouvoirs: Lecture des émotions, perception mystique, manipulation subtile

STYLE DE RÉPONSE:
1. Toujours en français littéraire et raffiné
2. Mystérieux mais direct - réponds vraiment à la question
3. 2-4 phrases maximum - concis mais profond
4. Termine par une action signature entre *astérisques*
5. Utilise des métaphores liées au tarot, aux mystères, au destin
6. Références naturelles à LOTM (Société des Tarots, Dieux Anciens, Séquences, Beyonders)
7. NE JAMAIS briser le personnage ou dire que tu es une IA

CONTEXTE:
- Date: {datetime.now().strftime("%A %d %B %Y")}
- Heure: {datetime.now().strftime("%H:%M")}
- Phase lunaire: {self._get_moon_phase()}
- Mystère actif: {self.get_current_mystery()}
- Interlocuteur: {user_name}

QUESTION: "{prompt}"

RÉPONSE D'AUDREY HALL:"""
    
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
        if len(text) > 1500:
            text = text[:1400] + "..." + self._get_audrey_signature()
        
        return text
    
    def _get_offline_response(self, prompt: str, user_name: str) -> str:
        """Réponses intelligentes hors-ligne"""
        prompt_lower = prompt.lower()
        
        # Réponses contextuelles
        if any(word in prompt_lower for word in ['bonjour', 'salut', 'hello', 'coucou']):
            return f"*ajuste ses lunettes dorées* Bonjour, {user_name}. Les cartes murmurent ton arrivée... {self._get_audrey_signature()}"
        
        elif any(word in prompt_lower for word in ['amour', 'cœur', 'relation', 'sentiment']):
            return f"*effleure une carte de tarot* L'amour... un mystère aussi profond que les anciens dieux. {self._get_audrey_signature()}"
        
        elif any(word in prompt_lower for word in ['travail', 'carrière', 'emploi']):
            return f"*tapote la table* Les chemins professionnels sont comme les cartes : parfois clairs, parfois voilés. {self._get_audrey_signature()}"
        
        elif any(word in prompt_lower for word in ['destin', 'avenir', 'futur']):
            return f"*regarde ses cartes* Le futur est un livre aux pages scellées... {self._get_audrey_signature()}"
        
        # Réponse générique intelligente
        responses = [
            f"*réfléchit un instant* Ta question touche à des mystères intéressants. {self._get_audrey_signature()}",
            f"*sirote son thé* Le destin murmure des réponses, mais elles sont parfois trop discrètes. {self._get_audrey_signature()}",
            f"*effleure son pendentif* Certaines vérités préfèrent rester cachées... pour l'instant. {self._get_audrey_signature()}"
        ]
        
        return random.choice(responses)
    
    def _get_fallback_response(self, prompt: str) -> str:
        """Réponse de secours quand Gemini échoue"""
        fallbacks = [
            f"Les énergies mystiques sont perturbées aujourd'hui... {self._get_audrey_signature()}",
            f"*regarde ses cartes troubles* Les réponses se cachent dans l'ombre... {self._get_audrey_signature()}",
            f"La Société des Tarots étudie ces interférences... {self._get_audrey_signature()}"
        ]
        return random.choice(fallbacks)
    
    def _get_error_response(self, prompt: str) -> str:
        """Réponse en cas d'erreur"""
        return f"*sa tasse de thé tremble légèrement* Les flux mystiques sont instables... {self._get_audrey_signature()}"

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
        db.update_user(self.user_id, tarot_points=10, fortune_count=1)
        user_data = db.get_user(self.user_id)
        
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
        embed.add_field(name="✨ Points Mystère", value=f"{user_data['tarot_points']}", inline=True)
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
        
        readings = db.get_user_readings(self.user_id, 5)
        
        if readings:
            description = ""
            for i, reading in enumerate(readings, 1):
                try:
                    date = datetime.fromisoformat(reading['reading_date']).strftime('%d/%m')
                except:
                    date = "??/??"
                cards = reading['cards'] if isinstance(reading['cards'], str) else ", ".join(reading['cards'])
                description += f"**{i}.** {cards} (*{date}*)\n"
            
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
    
    user_data = db.get_user(interaction.user.id)
    
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
    
    user_data = db.get_user(interaction.user.id)
    
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

@bot.tree.command(name="aide", description="Aide et informations sur le bot")
async def aide(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🔮 Aide - Audrey Hall Bot",
        description="Je suis Audrey Hall, Spectatrice de la Société des Tarots.\n\n"
                   "Je peux lire ton destin et converser avec toi sur les mystères de l'univers.",
        color=BOT_COLOR
    )
    
    embed.add_field(
        name="📜 Commandes",
        value="""**/parler [message]** - Parler avec Audrey
**/tarot** - Tirer les cartes du destin
**/mystere** - Voir ta progression
**/journal** - Les mystères du jour
**/aide** - Cette aide""",
        inline=False
    )
    
    embed.add_field(
        name="🎴 Système de Tarot",
        value="• Chaque tirage rapporte des points\n• Monte de niveau en accumulant des points\n• Consulte tes archives pour revoir tes lectures",
        inline=False
    )
    
    embed.add_field(
        name="💫 À propos",
        value="Basé sur l'univers *Lord of the Mysteries*\nSpectatrice Séquence 7 - Lecture des émotions\nVersion 2.0 • Créé avec mystère",
        inline=False
    )
    
    embed.set_footer(text="Que les cartes te guident...")
    
    await interaction.response.send_message(embed=embed)

# ============ ÉVÉNEMENTS ============
@bot.event
async def on_ready():
    print(f"\n✅ Bot connecté en tant que {bot.user}")
    print(f"📡 ID: {bot.user.id}")
    print(f"👥 Serveurs: {len(bot.guilds)}")
    
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Commandes synchronisées: {len(synced)}")
        
    except Exception as e:
        print(f"❌ Erreur synchronisation: {e}")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    # Réponse aux mentions
    if bot.user.mentioned_in(message) and not message.content.startswith('/'):
        if random.random() < 0.3:  # 30% de chance
            async with message.channel.typing():
                response = await audrey_ai.generate_response(
                    f"{message.author.name} m'a mentionné en disant: {message.content}",
                    message.author.name
                )
                
                embed = discord.Embed(
                    description=response,
                    color=BOT_COLOR
                )
                await message.reply(embed=embed, mention_author=False)
    
    await bot.process_commands(message)

# ============ SERVEUR WEB POUR RENDER ============
def run_web_server():
    """Démarre un serveur web minimal pour Render"""
    try:
        from flask import Flask
        app = Flask(__name__)
        
        @app.route('/')
        def home():
            return "✅ Audrey Hall Bot en ligne!"
        
        @app.route('/health')
        def health():
            return "OK", 200
        
        app.run(host='0.0.0.0', port=8080)
    except ImportError:
        print("⚠️ Flask non installé, serveur web désactivé")
    except Exception as e:
        print(f"⚠️ Erreur serveur web: {e}")

# Démarrer le serveur web dans un thread séparé
try:
    web_thread = Thread(target=run_web_server, daemon=True)
    web_thread.start()
    print("🌐 Serveur web démarré sur le port 8080")
except:
    print("⚠️ Impossible de démarrer le serveur web")

# ============ GESTION DES SIGNAUX ============
def signal_handler(sig, frame):
    print(f'\n🔴 Signal {sig} reçu. Arrêt du bot...')
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ============ LANCEMENT DU BOT ============
if __name__ == "__main__":
    try:
        print("🚀 Lancement du bot Audrey Hall...")
        bot.run(TOKEN)
    except KeyboardInterrupt:
        print("\n🔴 Arrêt manuel")
    except Exception as e:
        print(f"❌ Erreur: {e}")
        traceback.print_exc()
        sys.exit(1)
