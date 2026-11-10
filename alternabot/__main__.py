"""Point d'entrée : python -m alternabot <commande>."""
import sys

from .cli import main

sys.exit(main())
