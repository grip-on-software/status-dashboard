from typing import Any, Dict, Optional, Sequence

def start(configfiles: Optional[Sequence[Dict[str, Any]]] = None,
        daemonize: bool = False, environment: Optional[Dict[str, str]] = None,
        fastcgi: bool = False, scgi: bool = False,
        pidfile: Optional[str] = None, imports: Optional[Sequence[str]] = None,
        cgi: bool = False) -> None: ...
