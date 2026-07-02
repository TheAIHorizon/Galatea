"""MCP handler - Docker and Home Assistant control.

This handler processes Model Context Protocol commands for:
- Docker container management
- Home Assistant smart home control
"""
import base64

from .base import BaseHandler, HandlerContext
from ..core import (
    get_logger,
    synthesize_tts,
    ResponseType,
    Status,
    MCPAction,
)
from ..services.docker_service import docker_service
from ..services.homeassistant_service import ha_service

logger = get_logger(__name__)


class MCPHandler(BaseHandler):
    """Handles MCP commands - Docker and Home Assistant."""
    
    async def handle(self, ctx: HandlerContext) -> None:
        """Handle MCP command from data."""
        command = ctx.data.get("command", {})
        await self.handle_command(ctx, command)
    
    async def handle_command(self, ctx: HandlerContext, command: dict) -> None:
        """Handle MCP command (Docker, Home Assistant, etc.)."""
        action = command.get("action", "")
        
        try:
            await ctx.send_status(Status.PROCESSING)
            
            result_text = ""
            
            # =========================================
            # Docker Commands
            # =========================================
            if action == MCPAction.DOCKER_LIST.value:
                result_text = await self._docker_list(command)
            
            elif action == MCPAction.DOCKER_RESTART.value:
                result_text = await self._docker_restart(command)
            
            elif action == MCPAction.DOCKER_STATUS.value:
                result_text = await self._docker_status(command)
            
            elif action == MCPAction.DOCKER_LOGS.value:
                result_text = await self._docker_logs(command)
            
            # =========================================
            # Home Assistant Commands
            # =========================================
            elif action == MCPAction.HA_TURN_ON.value:
                result_text = await self._ha_turn_on(command)
            
            elif action == MCPAction.HA_TURN_OFF.value:
                result_text = await self._ha_turn_off(command)
            
            elif action == MCPAction.HA_SET_TEMPERATURE.value:
                result_text = await self._ha_set_temperature(command)
            
            elif action == MCPAction.HA_GET_STATE.value:
                result_text = await self._ha_get_state(command)
            
            elif action == MCPAction.HA_LIST_DEVICES.value:
                result_text = await self._ha_list_devices()
            
            else:
                result_text = f"Unknown MCP action: {action}"
            
            # Record in conversation
            ctx.state.messages.append({"role": "user", "content": f"[MCP Command: {action}]"})
            ctx.state.messages.append({"role": "assistant", "content": result_text})
            
            # Send response
            await ctx.send_response(ResponseType.LLM_COMPLETE, text=result_text)
            await ctx.send_status(Status.SPEAKING)
            await self._speak(ctx, result_text)
            await ctx.send_status(Status.IDLE)
            
        except Exception as e:
            logger.error(f"MCP command error: {e}", exc_info=True)
            error_msg = f"MCP command failed: {str(e)}"
            await ctx.send_error(error_msg)
            await ctx.send_status(Status.IDLE)
    
    # =========================================
    # Docker Methods
    # =========================================
    
    async def _docker_list(self, command: dict) -> str:
        """List Docker containers."""
        if not docker_service.is_available:
            return "I can't connect to Docker right now. Make sure Docker is running."
        
        containers = await docker_service.list_containers(all_containers=command.get("all", True))
        if not containers:
            return "No Docker containers found."
        
        running = [c for c in containers if c.status == 'running']
        stopped = [c for c in containers if c.status != 'running']
        
        lines = []
        if running:
            lines.append(f"Running ({len(running)}): " + ", ".join(c.name for c in running))
        if stopped:
            lines.append(f"Stopped ({len(stopped)}): " + ", ".join(c.name for c in stopped))
        
        return " ".join(lines)
    
    async def _docker_restart(self, command: dict) -> str:
        """Restart a Docker container."""
        container_name = command.get("container", "")
        if not docker_service.is_available:
            return "I can't connect to Docker right now."
        
        # Try to find container by partial name
        containers = await docker_service.list_containers(all_containers=True)
        matches = [c for c in containers if container_name.lower() in c.name.lower()]
        
        if not matches:
            return f"I couldn't find a container matching '{container_name}'."
        
        if len(matches) > 1:
            names = ", ".join(c.name for c in matches)
            return f"Multiple matches found: {names}. Please be more specific."
        
        container = matches[0]
        success, message = await docker_service.restart_container(container.name)
        return message if message else (
            f"Restarted container {container.name}." if success
            else f"Failed to restart {container.name}."
        )
    
    async def _docker_status(self, command: dict) -> str:
        """Get Docker container status."""
        container_name = command.get("container", "")
        if not docker_service.is_available:
            return "I can't connect to Docker right now."
        
        containers = await docker_service.list_containers(all_containers=True)
        matches = [c for c in containers if container_name.lower() in c.name.lower()]
        
        if not matches:
            return f"I couldn't find a container matching '{container_name}'."
        
        container = matches[0]
        health = await docker_service.get_container_health(container.name)

        if health and "error" not in health:
            return (
                f"{container.name} is {health['status']}. "
                f"CPU: {health['cpu_percent']}%, Memory: {health['memory_mb']}MB "
                f"({health['memory_percent']}%)"
            )
        else:
            return f"{container.name} is {container.status}."
    
    async def _docker_logs(self, command: dict) -> str:
        """Get Docker container logs."""
        container_name = command.get("container", "")
        lines = command.get("lines", 10)
        
        if not docker_service.is_available:
            return "I can't connect to Docker right now."
        
        containers = await docker_service.list_containers(all_containers=True)
        matches = [c for c in containers if container_name.lower() in c.name.lower()]
        
        if not matches:
            return f"I couldn't find a container matching '{container_name}'."
        
        container = matches[0]
        success, logs = await docker_service.get_logs(container.name, tail=lines)

        if success and logs:
            # Truncate for speech
            if len(logs) > 500:
                return f"Here are the recent logs for {container.name}: {logs[:500]}..."
            return f"Logs for {container.name}: {logs}"
        else:
            return f"No logs available for {container.name}."
    
    # =========================================
    # Home Assistant Methods
    # =========================================

    async def _ha_resolve_entity_id(self, command: dict) -> str:
        """Resolve an entity_id from the command, looking it up by device name if needed."""
        entity_id = command.get("entity_id", "")
        device_name = command.get("device", "")

        if not entity_id and device_name:
            states = await ha_service.get_states()
            match = ha_service.find_entity_by_name(states, device_name)
            entity_id = match.entity_id if match else ""

        return entity_id

    async def _ha_turn_on(self, command: dict) -> str:
        """Turn on a Home Assistant device."""
        if not ha_service.is_configured:
            return "Home Assistant is not configured. Please set HA_URL and HA_TOKEN in your environment."

        device_name = command.get("device", "")
        entity_id = await self._ha_resolve_entity_id(command)

        if not entity_id:
            return f"I couldn't find a device called '{device_name}'."

        success, message = await ha_service.turn_on(entity_id)

        if success:
            return f"Turned on {device_name or entity_id}."
        else:
            return f"Failed to turn on {device_name or entity_id}: {message}"

    async def _ha_turn_off(self, command: dict) -> str:
        """Turn off a Home Assistant device."""
        if not ha_service.is_configured:
            return "Home Assistant is not configured."

        device_name = command.get("device", "")
        entity_id = await self._ha_resolve_entity_id(command)

        if not entity_id:
            return f"I couldn't find a device called '{device_name}'."

        success, message = await ha_service.turn_off(entity_id)

        if success:
            return f"Turned off {device_name or entity_id}."
        else:
            return f"Failed to turn off {device_name or entity_id}: {message}"

    async def _ha_set_temperature(self, command: dict) -> str:
        """Set thermostat temperature."""
        if not ha_service.is_configured:
            return "Home Assistant is not configured."

        temperature = command.get("temperature")
        entity_id = await self._ha_resolve_entity_id(command)

        if not temperature:
            return "Please specify a temperature."

        if not entity_id:
            device_name = command.get("device", "")
            return f"I couldn't find a device called '{device_name}'."

        success, message = await ha_service.set_temperature(entity_id, float(temperature))

        if success:
            return f"Set temperature to {temperature} degrees."
        else:
            return f"Failed to set temperature: {message}"

    async def _ha_get_state(self, command: dict) -> str:
        """Get state of a Home Assistant entity."""
        if not ha_service.is_configured:
            return "Home Assistant is not configured."

        device_name = command.get("device", "")
        entity_id = await self._ha_resolve_entity_id(command)

        if not entity_id:
            return f"I couldn't find a device called '{device_name}'."

        state = await ha_service.get_state(entity_id)

        if state:
            return f"{device_name or entity_id} is {state.state}."
        else:
            return f"Couldn't get state for {device_name or entity_id}."

    async def _ha_list_devices(self) -> str:
        """List Home Assistant devices."""
        if not ha_service.is_configured:
            return "Home Assistant is not configured."

        devices = await ha_service.get_states()

        if not devices:
            return "No devices found in Home Assistant."

        # Group by domain
        by_domain = {}
        for d in devices[:20]:  # Limit for speech
            domain = d.entity_id.split(".")[0]
            by_domain.setdefault(domain, []).append(d.friendly_name or d.entity_id)

        parts = []
        for domain, names in by_domain.items():
            parts.append(f"{domain}: {', '.join(names[:5])}")

        return "Available devices: " + "; ".join(parts)
    
    async def _speak(self, ctx: HandlerContext, text: str) -> None:
        """Synthesize and send TTS audio."""
        try:
            audio_data = await synthesize_tts(
                text=text,
                voice=ctx.settings.selected_voice,
                provider=getattr(ctx.settings, 'tts_provider', 'piper'),
                speed=getattr(ctx.settings, 'voice_speed', 1.0)
            )
            if audio_data:
                await ctx.send_response(
                    ResponseType.AUDIO_CHUNK,
                    audio=base64.b64encode(audio_data).decode('utf-8'),
                    sentence=text
                )
        except Exception as e:
            logger.error(f"MCP TTS error: {e}")
